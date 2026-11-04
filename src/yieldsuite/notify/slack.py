"""Slack notifications for pipeline runs.

Two ways to post, picked from environment variables (never from the repo):

* Incoming webhook (``YIELDSUITE_SLACK_WEBHOOK_URL``): no extra dependency.
  A webhook is bound to the channel chosen when it was created.
* Bot token (``YIELDSUITE_SLACK_BOT_TOKEN``, needs ``yieldsuite[slack]``):
  posts to the configured channel (default ``#yield-data-suite``). The bot
  must be invited to private channels with ``/invite``.

Messages carry field ids, QA numbers and links only, never map files: yield
maps belong to the farmer.
"""

from __future__ import annotations

import json
import os
import urllib.request

from ..config import DEFAULT_SLACK_CHANNEL, SlackSettings
from ..pipeline import RunSummary


def format_summary(s: RunSummary) -> str:
    head = f"*{s.field_id} / {s.year}*"
    if not s.ok:
        return f":x: {head} run to {s.level.value} failed: {s.error}"
    parts = [
        f":white_check_mark: {head} → {s.level.value} ready",
        f"class `{s.product_class.value}`",
    ]
    if s.flagged_pct is not None:
        parts.append(f"flagged {s.flagged_pct:.0f}%")
    if s.k_factors:
        parts.append("k " + ", ".join(f"{m}={k:.3f}" for m, k in sorted(s.k_factors.items())))
    if s.mass_ok is not None:
        parts.append("mass check " + ("OK" if s.mass_ok else "*FAIL*"))
    parts.extend(s.notes)
    if s.viewer_url:
        parts.append(f"<{s.viewer_url}|open map>")
    return " · ".join(parts)


class SlackNotifier:
    def __init__(self, settings: SlackSettings | None = None, env: dict | None = None):
        self.settings = settings or SlackSettings()
        env = os.environ if env is None else env
        self.webhook_url = env.get(self.settings.webhook_url_env)
        self.bot_token = env.get(self.settings.bot_token_env)
        if not (self.webhook_url or self.bot_token):
            raise RuntimeError(
                f"set {self.settings.webhook_url_env} or {self.settings.bot_token_env} "
                "to enable Slack notifications"
            )

    @property
    def channel(self) -> str:
        return self.settings.channel or DEFAULT_SLACK_CHANNEL

    def send(self, text: str) -> None:
        if self.webhook_url:
            self._send_webhook(text)
        else:
            self._send_bot(text)

    def notify_run(self, summary: RunSummary) -> None:
        if summary.viewer_url is None and self.settings.viewer_base_url:
            summary.viewer_url = (
                f"{self.settings.viewer_base_url.rstrip('/')}/{summary.field_id}/{summary.year}"
            )
        self.send(format_summary(summary))

    def _send_webhook(self, text: str) -> None:
        req = urllib.request.Request(
            self.webhook_url,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - URL from env
            if resp.status != 200:
                raise RuntimeError(f"Slack webhook returned HTTP {resp.status}")

    def _send_bot(self, text: str) -> None:
        try:
            from slack_sdk import WebClient
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError("bot-token posting needs: pip install yieldsuite[slack]") from exc
        WebClient(token=self.bot_token).chat_postMessage(channel=self.channel, text=text)
