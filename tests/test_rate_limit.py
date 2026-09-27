"""Rate-limit header parsing, including absent and malformed metadata."""

import pytest

from ingestion.client.rate_limit import RateLimit


def test_headers_are_case_insensitive() -> None:
    rate = RateLimit.from_headers(
        {
            "X-RateLimit-Limit": "5000",
            "x-ratelimit-remaining": "15",
            "X-RATELIMIT-RESET": "1030",
            "Retry-After": "10",
        },
        now=1000,
    )
    assert rate == RateLimit(limit=5000, remaining=15, reset_at=1030, retry_after=10)
    assert rate.reset_delay(1000) == 31


def test_absent_headers() -> None:
    assert RateLimit.from_headers({}, now=1000) == RateLimit()
    assert RateLimit().reset_delay(1000) == 0


@pytest.mark.parametrize("value", ["invalid", "-1", "1.5", "nan", "inf", ""])
def test_malformed_headers(value: str) -> None:
    assert (
        RateLimit.from_headers(
            {
                "X-RateLimit-Limit": value,
                "X-RateLimit-Remaining": value,
                "X-RateLimit-Reset": value,
                "Retry-After": value,
            },
            now=1000,
        )
        == RateLimit()
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Thu, 01 Jan 1970 00:17:00 GMT", 20),
        ("Thu, 01 Jan 1970 00:17:00", 20),
        ("Thu, 01 Jan 1970 00:00:00 GMT", 0),
        ("0", 0),
    ],
)
def test_retry_after_date_and_zero(value: str, expected: float) -> None:
    assert (
        RateLimit.from_headers({"Retry-After": value}, now=1000).retry_after == expected
    )


def test_past_reset_never_causes_negative_sleep() -> None:
    assert RateLimit(reset_at=999).reset_delay(1000) == 0


def test_unrepresentable_deadlines_are_ignored() -> None:
    rate = RateLimit.from_headers(
        {"Retry-After": "9" * 400, "X-RateLimit-Reset": "9" * 400}, now=1000
    )
    assert rate.retry_after is None
    assert rate.reset_at is None
