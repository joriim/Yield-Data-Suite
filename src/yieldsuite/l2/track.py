"""Track geometry and time alignment of logged signals."""

from __future__ import annotations

import numpy as np


def shift_signal(t_s, values, tau_s: float, max_gap_s: float = 3.0):
    """Value of a signal ``tau_s`` seconds later: v(t + tau).

    Grain sensed at time t + tau_d was cut at time t, so shifting the mass flow
    by tau_d assigns each record the grain it actually cut. Where t + tau falls
    after the log ends or inside a logging gap longer than ``max_gap_s``, the
    result is NaN.
    """
    t = np.asarray(t_s, dtype=float)
    v = np.asarray(values, dtype=float)
    tq = t + tau_s
    out = np.interp(tq, t, v, left=np.nan, right=np.nan)
    idx = np.clip(np.searchsorted(t, tq), 1, len(t) - 1)
    gap = (t[idx] - t[idx - 1]) > max_gap_s
    out[gap | (tq > t[-1] + 1e-9)] = np.nan
    return out


def new_segments(t_s, max_gap_s: float = 3.0) -> np.ndarray:
    """True for the first record and for records after a logging gap."""
    t = np.asarray(t_s, dtype=float)
    return np.r_[True, np.diff(t) > max_gap_s]


def derive_heading(x, y, starts=None):
    """Heading [rad, clockwise from grid north] from successive positions.

    Record k drove from k-1 to k. Records in ``starts`` (first record, or the
    first after a logging gap) take the heading of the following segment.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2:
        return np.zeros_like(x)
    h = np.r_[np.nan, np.arctan2(np.diff(x), np.diff(y))]
    starts = np.zeros(x.shape, bool) if starts is None else np.asarray(starts, bool).copy()
    starts[0] = True
    nxt = np.r_[h[1:], np.nan]
    h = np.where(starts, nxt, h)
    return np.nan_to_num(h, nan=0.0)


def segment_lengths(x, y, speed=None, dt=None, starts=None):
    """Distance driven by each record (from the previous position).

    Records in ``starts`` (first record, or the first after a logging gap) fall
    back to speed * dt, so a jump between passes never becomes a long polygon.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    d = np.r_[np.nan, np.hypot(np.diff(x), np.diff(y))]
    if starts is not None:
        d = np.where(np.asarray(starts, bool), np.nan, d)
    if speed is not None and dt is not None:
        fallback = np.asarray(speed, dtype=float) * np.asarray(dt, dtype=float)
        d = np.where(np.isfinite(d), d, fallback)
    if np.isnan(d).any() and np.isfinite(d).any():
        d = np.where(np.isfinite(d), d, np.nanmedian(d))
    return np.nan_to_num(d, nan=0.0)


def segment_passes(
    t_s, harvesting, heading=None, max_gap_s: float = 3.0, max_turn_rad: float = np.pi / 4
):
    """Pass id per record: a new pass starts when harvesting resumes, after a
    logging gap, or after a sharp turn. Records not harvesting get -1.
    """
    t = np.asarray(t_s, dtype=float)
    on = np.asarray(harvesting, dtype=bool)
    new = np.zeros(t.shape, dtype=bool)
    new[0] = on[0]
    new[1:] = on[1:] & (~on[:-1] | (np.diff(t) > max_gap_s))
    if heading is not None:
        h = np.asarray(heading, dtype=float)
        turn = np.abs(np.angle(np.exp(1j * np.diff(h))))
        new[1:] |= on[1:] & (turn > max_turn_rad)
    pid = np.cumsum(new) - 1
    return np.where(on, pid, -1)
