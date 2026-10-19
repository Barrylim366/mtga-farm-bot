"""Shared defaults for persisted gameplay settings."""

DEFAULT_MATCH_TIMEOUT_MINUTES = 10


def match_timeout_minutes(value) -> int:
    """Read stored minutes strictly; booleans and fractional values are invalid."""
    if type(value) is int and value >= 0:
        return value
    return DEFAULT_MATCH_TIMEOUT_MINUTES
