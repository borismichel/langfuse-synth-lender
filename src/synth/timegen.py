"""Time distribution (spec §5): diurnal + weekly weighting so time-series views look real.

We build an hourly weight curve across the window (business-hours peaks, overnight
troughs, weekend dip), then sample each trace's timestamp proportionally and jitter
within its hour. ``run_date`` is the anchor; all "relative to now" offsets snap to it
(spec §9), keeping the grant date and drift window recent on every run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .rng import Rng

# Diurnal shape: relative weight per local hour (0-23). Peaks mid-morning & mid-afternoon.
_DIURNAL = [
    0.05, 0.03, 0.02, 0.02, 0.03, 0.06,  # 00-05 overnight trough
    0.15, 0.35, 0.70, 0.95, 1.00, 0.95,  # 06-11 morning ramp -> peak
    0.80, 0.85, 0.95, 0.90, 0.75, 0.55,  # 12-17 afternoon
    0.40, 0.30, 0.22, 0.16, 0.10, 0.07,  # 18-23 evening wind-down
]
# Weekly shape: Mon..Sun. Weekend dip.
_WEEKLY = [1.0, 1.0, 1.05, 1.0, 0.9, 0.45, 0.35]


def hour_weight(dt: datetime, tz_offset_hours: int = 0) -> float:
    """Diurnal × weekly weight for a UTC timestamp, evaluated in local business time
    (spec v2: Europe/Berlin curve — business-hours density, lunch dip, and a
    Friday-afternoon decline)."""
    local_hour = (dt.hour + tz_offset_hours) % 24
    w = _DIURNAL[local_hour] * _WEEKLY[dt.weekday()]
    if dt.weekday() == 4 and local_hour >= 14:  # Friday afternoon winds down early
        w *= 0.5
    return w


def sample_session_times(rng: Rng, run_date: datetime, window_days: int,
                         weekday_range: tuple[int, int], weekend_range: tuple[int, int],
                         scale: float, tz_offset_hours: int) -> list[datetime]:
    """Session-start timestamps, sessions-per-day driven (spec v2 §3): weekday-weighted
    counts, business-hours intraday placement. Total volume is derived, not forced."""
    start = window_start(run_date, window_days)
    r = rng.sub("sessiontimes")
    out: list[datetime] = []
    for d in range(window_days):
        day = start + timedelta(days=d)
        lo, hi = weekend_range if day.weekday() >= 5 else weekday_range
        n = max(0, int(round(r.randint(lo, hi) * scale)))
        if n == 0:
            continue
        hours = [day + timedelta(hours=h) for h in range(24)]
        weights = [hour_weight(h, tz_offset_hours) for h in hours]
        for h in r.choices(hours, weights, k=n):
            ts = h + timedelta(seconds=r.uniform(0, 3600))
            if ts < run_date:
                out.append(ts)
    out.sort()
    return out


def window_start(run_date: datetime, window_days: int) -> datetime:
    """Midnight UTC, ``window_days`` before the run date."""
    start = run_date - timedelta(days=window_days)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def sample_timestamps(rng: Rng, run_date: datetime, window_days: int, n: int) -> list[datetime]:
    """Return ``n`` timestamps over the window, diurnally/weekly weighted, sorted ascending."""
    start = window_start(run_date, window_days)
    total_hours = window_days * 24
    hours = [start + timedelta(hours=h) for h in range(total_hours)]
    weights = [hour_weight(h) for h in hours]

    rsub = rng.sub("timegen")
    chosen_hours = rsub.choices(hours, weights, k=n)
    out: list[datetime] = []
    for h in chosen_hours:
        jitter = rsub.uniform(0, 3600)  # seconds within the hour
        out.append(h + timedelta(seconds=jitter))
    out.sort()
    return out


def sample_in_range(rng: Rng, start: datetime, end: datetime, n: int, label: str = "range",
                    ramp: float | None = None) -> list[datetime]:
    """Sample ``n`` diurnally/weekly-weighted timestamps within an arbitrary [start, end).

    If ``ramp`` is given (0 < ramp <= 1), multiply in a linear weight rising from
    ``ramp`` at ``start`` to 1.0 at ``end`` — biasing the draw toward ``end`` so the
    resulting volume *climbs* across the range (e.g. an appeal rate trending up to now)."""
    start = start.replace(minute=0, second=0, microsecond=0)
    total_hours = max(1, int((end - start).total_seconds() // 3600))
    hours = [start + timedelta(hours=h) for h in range(total_hours)]
    weights = [hour_weight(h) for h in hours] or [1.0]
    if ramp is not None and total_hours > 1:
        span = total_hours - 1
        weights = [w * (ramp + (1.0 - ramp) * (i / span)) for i, w in enumerate(weights)]
    rsub = rng.sub("timegen", label)
    out = []
    for h in rsub.choices(hours, weights, k=n):
        out.append(h + timedelta(seconds=rsub.uniform(0, 3600)))
    out.sort()
    return out


def in_window(ts: datetime, start: datetime, end: datetime) -> bool:
    return start <= ts < end


def day_anchor(run_date: datetime, day_offset: int) -> datetime:
    """A timestamp ``day_offset`` days from the run date (offset is typically negative)."""
    return (run_date + timedelta(days=day_offset)).replace(microsecond=0)


def now_utc() -> datetime:
    """The single wall-clock read in the whole program — the run anchor (spec §9).

    Captured once at the start of a command and threaded through as ``run_date`` so the
    rest of the seed path stays deterministic.
    """
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    """ISO-8601 with milliseconds and a trailing Z, as the ingestion API expects."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def iso_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")
