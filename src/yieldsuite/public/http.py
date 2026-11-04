"""Minimal JSON over HTTPS with a generic client name (no personal data in headers)."""

from __future__ import annotations

import json
import urllib.request

USER_AGENT = "yieldsuite"


def get_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured URL
        return json.loads(resp.read().decode())


def post_json(url: str, body: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/geo+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - configured URL
        return json.loads(resp.read().decode())
