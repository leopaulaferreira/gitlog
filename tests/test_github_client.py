"""Behavioral tests using HTTPX MockTransport and a simulated wall clock."""

import logging
import traceback
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from ingestion.client import GitHubClient
from ingestion.client.exceptions import (
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubForbiddenError,
    GitHubHTTPError,
    GitHubNotFoundError,
    GitHubPaginationError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubTimeoutError,
    GitHubTransportError,
)

TOKEN = "test-token-not-a-real-secret"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(clock: FakeClock) -> Iterator[Callable[..., GitHubClient]]:
    clients: list[GitHubClient] = []

    def create(handler: Callable[..., httpx.Response], **kwargs: Any) -> GitHubClient:
        client = GitHubClient(
            transport=httpx.MockTransport(handler),
            sleep=clock.sleep,
            clock=clock.time,
            **kwargs,
        )
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


def test_authenticated_get_headers_params_and_timeout(make_client: Callable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert dict(request.url.params) == {"state": "all", "page": "2"}
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        assert request.headers["User-Agent"] == "GitLog"
        assert set(request.extensions["timeout"].values()) == {7.0}
        return httpx.Response(200, json={"id": 123, "description": None})

    client = make_client(handler, timeout=7)
    assert client.get("/repos/example/project", params={"state": "all", "page": 2}) == {
        "id": 123,
        "description": None,
    }


@pytest.mark.parametrize("token", [None, "", " ", "abc\ndef", "abc\x00", "tökén"])
def test_token_is_required_and_validated(
    token: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if token is None:
        monkeypatch.delenv("GITHUB_TOKEN")
    else:
        # NUL cannot be represented in os.environ; patch its accessor instead.
        monkeypatch.setattr(
            "ingestion.client.github_client.os.environ.get", lambda *_: token
        )
    with pytest.raises(GitHubConfigurationError, match="GITHUB_TOKEN"):
        GitHubClient()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"timeout": True},
        {"timeout": "30"},
        {"max_retries": -1},
        {"max_retries": 11},
        {"max_retries": 1.5},
        {"max_retries": True},
        {"backoff_factor": 0},
        {"max_wait": 0},
        {"rate_limit_threshold": -1},
        {"rate_limit_threshold": 1.5},
    ],
)
def test_invalid_configuration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(GitHubConfigurationError):
        GitHubClient(**kwargs)


def test_context_manager_closes_transport_on_error() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    transport.close = Mock()
    with pytest.raises(RuntimeError, match="caller failed"):
        with GitHubClient(transport=transport) as client:
            assert client.get("/user") == {}
            raise RuntimeError("caller failed")
    transport.close.assert_called_once()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_follows_same_origin_redirects(make_client: Callable, status: int) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(status, headers={"Location": "/repositories/123"})
        assert request.url.path == "/repositories/123"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"id": 123})

    assert make_client(handler).get("/repos/old/name") == {"id": 123}
    assert len(requests) == 2


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/path",
        "http://api.github.com/path",
        "https://api.github.com:8443/path",
        "https://user:password@api.github.com/path",
        "//example.com/path",
        "https://api.github.com/path#fragment",
        "https://[invalid",
        "https://api.github.com:invalid/path",
    ],
)
def test_rejects_unsafe_initial_urls(make_client: Callable, target: str) -> None:
    handler = Mock()
    with pytest.raises(GitHubConfigurationError):
        make_client(handler).get(target)
    handler.assert_not_called()


@pytest.mark.parametrize("kind", ["redirect", "pagination"])
@pytest.mark.parametrize("target", ["https://evil.example/steal", "https://[invalid"])
def test_rejects_unsafe_server_links(
    make_client: Callable, kind: str, target: str
) -> None:
    response = (
        httpx.Response(302, headers={"Location": target})
        if kind == "redirect"
        else httpx.Response(200, json=[], headers={"Link": f'<{target}>; rel="next"'})
    )
    handler = Mock(return_value=response)
    client = make_client(handler)
    with pytest.raises(GitHubConfigurationError):
        if kind == "redirect":
            client.get("/repos/example/project")
        else:
            list(client.get_paginated("/repos/example/project/commits"))
    assert handler.call_count == 1


def test_redirect_loop_is_bounded(make_client: Callable) -> None:
    handler = Mock(
        side_effect=lambda _: httpx.Response(301, headers={"Location": "/loop"})
    )
    with pytest.raises(GitHubResponseError, match="redirects"):
        make_client(handler).get("/loop")
    assert handler.call_count == 6


def test_missing_redirect_location(make_client: Callable) -> None:
    with pytest.raises(GitHubResponseError, match="redirects"):
        make_client(lambda _: httpx.Response(302)).get("/repos/example/project")


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, GitHubAuthenticationError),
        (403, GitHubForbiddenError),
        (404, GitHubNotFoundError),
        (400, GitHubHTTPError),
        (410, GitHubHTTPError),
        (422, GitHubHTTPError),
        (304, GitHubHTTPError),
    ],
)
def test_permanent_http_errors_are_not_retried(
    make_client: Callable, clock: FakeClock, status: int, error: type[Exception]
) -> None:
    handler = Mock(return_value=httpx.Response(status, json={"message": TOKEN}))
    with pytest.raises(error) as caught:
        make_client(handler).get("/repos/example/project")
    assert caught.value.status_code == status
    assert TOKEN not in str(caught.value)
    assert handler.call_count == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_retry_exponentially(
    make_client: Callable, clock: FakeClock, status: int
) -> None:
    handler = Mock(
        side_effect=[httpx.Response(status) for _ in range(3)]
        + [httpx.Response(200, json={"ok": True})]
    )
    assert make_client(handler).get("/user") == {"ok": True}
    assert handler.call_count == 4
    assert clock.sleeps == [1, 2, 4]


def test_server_error_retry_exhaustion(make_client: Callable, clock: FakeClock) -> None:
    handler = Mock(side_effect=lambda _: httpx.Response(503))
    with pytest.raises(GitHubHTTPError) as caught:
        make_client(handler, max_retries=2).get("/user")
    assert caught.value.status_code == 503
    assert handler.call_count == 3
    assert clock.sleeps == [1, 2]


@pytest.mark.parametrize(
    ("transport_error", "client_error"),
    [
        (httpx.ConnectError, GitHubTransportError),
        (httpx.RemoteProtocolError, GitHubTransportError),
        (httpx.ConnectTimeout, GitHubTimeoutError),
        (httpx.ReadTimeout, GitHubTimeoutError),
        (httpx.WriteTimeout, GitHubTimeoutError),
        (httpx.PoolTimeout, GitHubTimeoutError),
    ],
)
def test_transport_failures_are_retried_and_sanitized(
    make_client: Callable,
    clock: FakeClock,
    transport_error: type[httpx.TransportError],
    client_error: type[Exception],
) -> None:
    handler = Mock(side_effect=transport_error(f"sensitive data: {TOKEN}"))
    with pytest.raises(client_error) as caught:
        make_client(handler, max_retries=2).get("/user")
    assert handler.call_count == 3
    assert clock.sleeps == [1, 2]
    assert TOKEN not in "".join(traceback.format_exception(caught.value))


def test_connection_failure_can_recover(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(
        side_effect=[httpx.ConnectError("offline"), httpx.Response(200, json=[])]
    )
    assert make_client(handler).get("/repositories") == []
    assert clock.sleeps == [1]


def test_zero_retries_and_backoff_cap(make_client: Callable, clock: FakeClock) -> None:
    handler = Mock(side_effect=lambda _: httpx.Response(500))
    with pytest.raises(GitHubHTTPError):
        make_client(handler, max_retries=0).get("/user")
    assert handler.call_count == 1
    assert clock.sleeps == []
    with pytest.raises(GitHubHTTPError):
        make_client(handler, backoff_factor=4, max_wait=5).get("/user")
    assert clock.sleeps == [4, 5, 5]


@pytest.mark.parametrize("content", [b"not json", b"null", b"42", b'"text"', b"\xff"])
def test_invalid_success_payload_is_not_retried(
    make_client: Callable, content: bytes
) -> None:
    handler = Mock(return_value=httpx.Response(200, content=content))
    with pytest.raises(GitHubResponseError):
        make_client(handler).get("/user")
    assert handler.call_count == 1


def test_no_content_response(make_client: Callable) -> None:
    assert make_client(lambda _: httpx.Response(204)).get("/empty") is None


def test_lazy_pagination_follows_next_and_preserves_server_query(
    make_client: Callable,
) -> None:
    requests = []
    params = {"since": "2026-09-26T00:00:00Z"}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            assert request.url.params["per_page"] == "100"
            assert request.url.params["since"] == params["since"]
            return httpx.Response(
                200,
                json=[{"sha": "a"}, {"sha": "b"}],
                headers={
                    "Link": (
                        "<https://api.github.com/commits?cursor=opaque&per_page=50>; "
                        'rel="next", '
                        '<https://api.github.com/commits?page=9>; rel="last"'
                    )
                },
            )
        assert (
            str(request.url)
            == "https://api.github.com/commits?cursor=opaque&per_page=50"
        )
        return httpx.Response(
            200,
            json=[{"sha": "c"}],
            headers={"Link": '<https://api.github.com/commits?page=1>; rel="prev"'},
        )

    pages = make_client(handler).get_paginated("/commits", params=params)
    assert requests == []
    assert next(pages) == {"sha": "a"}
    assert len(requests) == 1
    assert list(pages) == [{"sha": "b"}, {"sha": "c"}]
    assert len(requests) == 2
    assert params == {"since": "2026-09-26T00:00:00Z"}


def test_custom_page_size_and_empty_page_with_next(make_client: Callable) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            assert request.url.params["per_page"] == "25"
            return httpx.Response(
                200, json=[], headers={"Link": '<?page=2>; rel="next"'}
            )
        assert dict(request.url.params) == {"page": "2"}
        return httpx.Response(200, json=[{"id": 1}])

    assert list(make_client(handler).get_paginated("/repositories", per_page=25)) == [
        {"id": 1}
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"per_page": 0},
        {"per_page": 101},
        {"per_page": True},
        {"per_page": 2.5},
        {"max_pages": 0},
        {"max_pages": True},
    ],
)
def test_pagination_configuration(make_client: Callable, kwargs: dict) -> None:
    handler = Mock()
    with pytest.raises(GitHubConfigurationError):
        list(make_client(handler).get_paginated("/commits", **kwargs))
    handler.assert_not_called()


@pytest.mark.parametrize("payload", [{"items": []}, [1], [None], [{"id": 1}, "bad"]])
def test_pagination_rejects_unexpected_shapes(
    make_client: Callable, payload: Any
) -> None:
    with pytest.raises(GitHubPaginationError):
        list(
            make_client(lambda _: httpx.Response(200, json=payload)).get_paginated(
                "/commits"
            )
        )


def test_empty_pagination(make_client: Callable) -> None:
    assert (
        list(
            make_client(lambda _: httpx.Response(200, json=[])).get_paginated(
                "/commits"
            )
        )
        == []
    )


def test_pagination_cycle(make_client: Callable) -> None:
    handler = Mock(
        side_effect=lambda request: httpx.Response(
            200, json=[], headers={"Link": f'<{request.url}>; rel="next"'}
        )
    )
    with pytest.raises(GitHubPaginationError, match="cycle"):
        list(make_client(handler).get_paginated("/commits"))
    assert handler.call_count == 1


def test_page_budget_raises_instead_of_silent_truncation(make_client: Callable) -> None:
    handler = Mock(
        return_value=httpx.Response(
            200, json=[], headers={"Link": '<?page=2>; rel="next"'}
        )
    )
    with pytest.raises(GitHubPaginationError, match="max_pages"):
        list(make_client(handler).get_paginated("/commits", max_pages=1))
    assert handler.call_count == 1


def test_later_page_retries_without_repeating_earlier_items(
    make_client: Callable,
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                200, json=[{"sha": "a"}], headers={"Link": '<?page=2>; rel="next"'}
            ),
            httpx.Response(502),
            httpx.Response(200, json=[{"sha": "b"}]),
        ]
    )
    assert list(make_client(handler).get_paginated("/commits")) == [
        {"sha": "a"},
        {"sha": "b"},
    ]
    assert handler.call_count == 3


def test_later_page_error_propagates(make_client: Callable) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                200, json=[{"sha": "a"}], headers={"Link": '<?page=2>; rel="next"'}
            ),
            httpx.Response(404),
        ]
    )
    iterator = make_client(handler).get_paginated("/commits")
    assert next(iterator) == {"sha": "a"}
    with pytest.raises(GitHubNotFoundError):
        next(iterator)


def test_rate_headers_warning_and_cooldown_on_next_request(
    make_client: Callable, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                200,
                json={"ok": True},
                headers={
                    "X-RateLimit-Limit": "5000",
                    "X-RateLimit-Remaining": "10",
                    "X-RateLimit-Reset": "1010",
                },
            ),
            httpx.Response(200, json={}),
        ]
    )
    client = make_client(handler)
    assert client.get("/user") == {"ok": True}
    assert client.rate_limit.limit == 5000
    assert client.rate_limit.remaining == 10
    assert client.rate_limit.reset_at == 1010
    assert clock.sleeps == []
    assert "github.rate_limit.low" in caplog.text
    client.get("/user")
    assert clock.sleeps == [11]


@pytest.mark.parametrize("status", [403, 429])
def test_exhausted_primary_rate_limit_waits_for_reset(
    make_client: Callable, clock: FakeClock, status: int
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                status,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1020"},
            ),
            httpx.Response(200, json={}),
        ]
    )
    assert make_client(handler).get("/user") == {}
    assert clock.sleeps == [21]


@pytest.mark.parametrize("status", [403, 429, 503])
def test_retry_after_is_respected(
    make_client: Callable, clock: FakeClock, status: int
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(status, headers={"Retry-After": "5"}),
            httpx.Response(200, json={}),
        ]
    )
    assert make_client(handler).get("/user") == {}
    assert sum(clock.sleeps) == 5


def test_retry_after_http_date(make_client: Callable, clock: FakeClock) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                429, headers={"Retry-After": "Thu, 01 Jan 1970 00:17:00 GMT"}
            ),
            httpx.Response(200, json={}),
        ]
    )
    make_client(handler).get("/user")
    assert clock.sleeps == [20]


def test_both_rate_limit_hints_use_later_deadline(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                429,
                headers={
                    "Retry-After": "5",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "1020",
                },
            ),
            httpx.Response(200, json={}),
        ]
    )
    make_client(handler).get("/user")
    assert clock.sleeps == [21]


@pytest.mark.parametrize(
    "message",
    [
        "You have exceeded a secondary rate limit.",
        "API rate limit exceeded",
        "Abuse detection mechanism triggered",
    ],
)
def test_secondary_rate_limit_exponential_fallback(
    make_client: Callable, clock: FakeClock, message: str
) -> None:
    handler = Mock(
        side_effect=[httpx.Response(403, json={"message": message}) for _ in range(2)]
        + [httpx.Response(200, json={})]
    )
    make_client(handler).get("/user")
    assert clock.sleeps == [60, 120]


def test_rate_limit_retry_exhaustion_and_persistent_cooldown(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(side_effect=lambda _: httpx.Response(429))
    client = make_client(handler, max_retries=1)
    with pytest.raises(GitHubRateLimitError) as caught:
        client.get("/user")
    assert caught.value.status_code == 429
    assert caught.value.retry_after == 120
    assert handler.call_count == 2
    assert clock.sleeps == [60]
    # A new call on the same client must still honor the previous cooldown.
    with pytest.raises(GitHubRateLimitError):
        client.get("/user")
    assert clock.sleeps[:2] == [60, 120]


def test_excessive_wait_fails_without_early_retry(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    client = make_client(handler)
    for _ in range(2):
        with pytest.raises(GitHubRateLimitError) as caught:
            client.get("/user")
        assert caught.value.retry_after == 3600
    assert handler.call_count == 1
    assert clock.sleeps == []


def test_proactive_limit_does_not_discard_successful_response(
    make_client: Callable,
) -> None:
    handler = Mock(
        return_value=httpx.Response(
            200,
            json={"id": 1},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "5000"},
        )
    )
    client = make_client(handler)
    assert client.get("/user") == {"id": 1}
    with pytest.raises(GitHubRateLimitError):
        client.get("/user")
    assert handler.call_count == 1


@pytest.mark.parametrize(
    "headers", [{}, {"Retry-After": "invalid", "X-RateLimit-Reset": "invalid"}]
)
def test_429_without_usable_hints_waits_a_minute(
    make_client: Callable, clock: FakeClock, headers: dict
) -> None:
    handler = Mock(
        side_effect=[httpx.Response(429, headers=headers), httpx.Response(200, json={})]
    )
    make_client(handler).get("/user")
    assert clock.sleeps == [60]


@pytest.mark.parametrize("body", [b"html error", b"[]", b'{"message": 123}'])
def test_403_without_rate_signal_is_not_retried(
    make_client: Callable, body: bytes
) -> None:
    handler = Mock(return_value=httpx.Response(403, content=body))
    with pytest.raises(GitHubForbiddenError):
        make_client(handler).get("/user")
    assert handler.call_count == 1


def test_logs_are_structured_and_do_not_include_credentials(
    make_client: Callable, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ingestion.client.github_client")
    handler = Mock(
        side_effect=[
            httpx.Response(503, json={"message": TOKEN}),
            httpx.Response(200, json={}),
        ]
    )
    make_client(handler).get("/user")
    records = [
        record
        for record in caplog.records
        if record.name == "ingestion.client.github_client"
    ]
    assert {record.event for record in records} == {"github.request", "github.retry"}
    assert TOKEN not in caplog.text
    assert TOKEN not in repr([vars(record) for record in records])


def test_pagination_cycle_via_redirect_does_not_duplicate_items(
    make_client: Callable,
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                200,
                json=[{"id": 1}],
                headers={"Link": '<?page=2>; rel="next"'},
            ),
            httpx.Response(301, headers={"Location": "/commits?per_page=100"}),
            httpx.Response(200, json=[{"id": 1}]),
        ]
    )
    iterator = make_client(handler).get_paginated("/commits")
    assert next(iterator) == {"id": 1}
    with pytest.raises(GitHubPaginationError, match="cycle"):
        next(iterator)
    assert handler.call_count == 3


def test_pagination_rejects_empty_next_url(make_client: Callable) -> None:
    handler = Mock(
        return_value=httpx.Response(200, json=[], headers={"Link": '<>; rel="next"'})
    )
    with pytest.raises(GitHubPaginationError, match="Missing URL"):
        list(make_client(handler).get_paginated("/commits"))
    assert handler.call_count == 1


@pytest.mark.parametrize(("remaining", "delay"), [(0, 60), (1, 1), (11, 0)])
def test_rate_limit_without_reset_uses_safe_pause(
    make_client: Callable, clock: FakeClock, remaining: int, delay: int
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(
                200, json={}, headers={"X-RateLimit-Remaining": str(remaining)}
            ),
            httpx.Response(200, json={}),
        ]
    )
    client = make_client(handler)
    client.get("/user")
    client.get("/user")
    assert sum(clock.sleeps) == delay


def test_custom_threshold_allows_remaining_quota(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(
        side_effect=lambda _: httpx.Response(
            200,
            json={},
            headers={"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": "1100"},
        )
    )
    client = make_client(handler, rate_limit_threshold=0)
    client.get("/user")
    client.get("/user")
    assert clock.sleeps == []


def test_pagination_completes_exactly_at_page_budget(make_client: Callable) -> None:
    assert list(
        make_client(lambda _: httpx.Response(200, json=[{"id": 1}])).get_paginated(
            "/repositories", max_pages=1
        )
    ) == [{"id": 1}]


def test_wait_equal_to_budget_is_allowed(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "300"}),
            httpx.Response(200, json={}),
        ]
    )
    make_client(handler).get("/user")
    assert clock.sleeps == [300]


def test_cooldown_expires_as_clock_advances(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3600"}),
            httpx.Response(200, json={}),
        ]
    )
    client = make_client(handler)
    with pytest.raises(GitHubRateLimitError):
        client.get("/user")
    clock.now += 3600
    assert client.get("/user") == {}
    assert handler.call_count == 2
    assert clock.sleeps == []


def test_redirects_do_not_reset_retry_budget(
    make_client: Callable, clock: FakeClock
) -> None:
    handler = Mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(301, headers={"Location": "/repositories/123"}),
            httpx.Response(503),
        ]
    )
    with pytest.raises(GitHubHTTPError):
        make_client(handler, max_retries=1).get("/repos/old/name")
    assert handler.call_count == 3
    assert clock.sleeps == [1]
