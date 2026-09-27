"""Parse GitHub rate limit headers without depending on domain entities."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime


def _nonnegative_int(value: str | None) -> int | None:
    try:
        number = int(value) if value is not None else -1
        return number if number >= 0 else None
    except ValueError:
        return None


def _retry_after(value: str | None, now: float) -> float | None:
    if value is None:
        return None
    seconds = _nonnegative_int(value)
    if seconds is not None:
        try:
            return float(seconds)
        except OverflowError:
            return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(0.0, date.timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass(frozen=True)
class RateLimit:
    limit: int | None = None
    remaining: int | None = None
    reset_at: int | None = None
    retry_after: float | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str], *, now: float) -> "RateLimit":
        normalized = {key.lower(): value for key, value in headers.items()}
        reset_at = _nonnegative_int(normalized.get("x-ratelimit-reset"))
        try:
            if reset_at is not None:
                float(reset_at)  # Check whether this can be used by the clock.
        except OverflowError:
            reset_at = None
        return cls(
            limit=_nonnegative_int(normalized.get("x-ratelimit-limit")),
            remaining=_nonnegative_int(normalized.get("x-ratelimit-remaining")),
            reset_at=reset_at,
            retry_after=_retry_after(normalized.get("retry-after"), now),
        )

    def reset_delay(self, now: float) -> float:
        # One second of margin: GitHub requires waiting until AFTER the reset.
        return max(0.0, self.reset_at - now + 1) if self.reset_at is not None else 0.0
