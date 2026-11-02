"""Shared defaults for persisted gameplay settings."""

import math

DEFAULT_MATCH_TIMEOUT_MINUTES = 10
DEFAULT_MAX_PLAY_HOURS = 0.0


def parse_max_play_hours(value) -> float:
    """Accept either decimal separator, without enabling invalid deadlines."""
    if isinstance(value, bool):
        raise ValueError("Enter a nonnegative number of hours (0 = off).")
    try:
        hours = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Enter a nonnegative number of hours (0 = off).") from None
    if not math.isfinite(hours) or hours < 0 or not math.isfinite(hours * 3600):
        raise ValueError("Enter a finite, nonnegative number of hours (0 = off).")
    return hours


def match_timeout_minutes(value) -> int:
    """Read stored minutes strictly; booleans and fractional values are invalid."""
    if type(value) is int and value >= 0:
        return value
    return DEFAULT_MATCH_TIMEOUT_MINUTES
