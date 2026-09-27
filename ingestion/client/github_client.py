"""Read-only GitHub REST access with bounded retries and lazy pagination."""

import logging
import math
import os
import time
from collections.abc import Callable, Iterator, Mapping
from types import TracebackType
from typing import Any, Self

import httpx

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
from ingestion.client.rate_limit import RateLimit

logger = logging.getLogger(__name__)
type QueryParams = Mapping[str, str | int | float | bool | None]
type Payload = dict[str, Any] | list[Any] | None


class GitHubClient:
    """Owns one HTTP session; use as a context manager, sequentially.

    Credentials come only from GITHUB_TOKEN. Environment files are not loaded
    implicitly. ``max_retries`` excludes the initial attempt. ``max_wait`` bounds
    each sleep, never truncating a wait requested by the server.
    """

    BASE_URL = "https://api.github.com"
    API_VERSION = "2026-03-10"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        max_wait: float = 300.0,
        rate_limit_threshold: int = 10,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token or not token.isascii() or any(c.isspace() for c in token):
            raise GitHubConfigurationError("Set a nonempty, valid GITHUB_TOKEN.")
        if any(ord(c) < 33 or ord(c) == 127 for c in token):
            raise GitHubConfigurationError("Set a nonempty, valid GITHUB_TOKEN.")
        for name, value in (
            ("timeout", timeout),
            ("backoff_factor", backoff_factor),
            ("max_wait", max_wait),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise GitHubConfigurationError(f"{name} must be finite and positive.")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise GitHubConfigurationError("max_retries must be between 0 and 10.")
        if type(rate_limit_threshold) is not int or rate_limit_threshold < 0:
            raise GitHubConfigurationError("rate_limit_threshold must be nonnegative.")
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._max_wait = max_wait
        self._threshold = rate_limit_threshold
        self._sleep = sleep
        self._clock = clock
        self._not_before = 0.0
        self._rate_status: int | None = None
        self.rate_limit = RateLimit()
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": self.API_VERSION,
                "User-Agent": "GitLog",
            },
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get(self, path: str, *, params: QueryParams | None = None) -> Payload:
        """Return decoded JSON (or None for 204); never transform domain fields."""
        return self._decode(self._request(self._url(path, params)))

    def get_paginated(
        self,
        path: str,
        *,
        params: QueryParams | None = None,
        per_page: int = 100,
        max_pages: int = 10_000,
    ) -> Iterator[dict[str, Any]]:
        """Yield objects from array endpoints, following Link rel=next verbatim.

        Validation and I/O are lazy. Previously yielded items remain consumed if
        a later page fails; callers must not interpret a failed iterator as complete.
        ``per_page`` overrides any value in params on the first page only.
        """
        for payload in self.get_pages(
            path, params=params, per_page=per_page, max_pages=max_pages
        ):
            if not isinstance(payload, list) or any(
                not isinstance(item, dict) for item in payload
            ):
                raise GitHubPaginationError("Expected a JSON array of objects.")
            yield from payload

    def get_pages(
        self,
        path: str,
        *,
        params: QueryParams | None = None,
        per_page: int = 100,
        max_pages: int = 10_000,
    ) -> Iterator[Payload]:
        """Yield original JSON pages, including object envelopes such as compare.

        Follow Link rel=next with the same origin, cycle and retry guarantees as
        get_paginated. Consumers must exhaust the iterator before checkpointing.
        """
        if type(per_page) is not int or not 1 <= per_page <= 100:
            raise GitHubConfigurationError("per_page must be between 1 and 100.")
        if type(max_pages) is not int or max_pages < 1:
            raise GitHubConfigurationError("max_pages must be a positive integer.")
        query = dict(params or {})
        query["per_page"] = per_page
        url = self._url(path, query)
        visited: set[httpx.URL] = set()
        for _ in range(max_pages):
            if url in visited:
                raise GitHubPaginationError("GitHub pagination contains a cycle.")
            visited.add(url)
            response = self._request(url)
            if response.url != url and response.url in visited:
                raise GitHubPaginationError("GitHub pagination contains a cycle.")
            visited.add(response.url)
            yield self._decode(response)
            next_link = response.links.get("next")
            if next_link is None:
                return
            target = next_link.get("url")
            if not target:
                raise GitHubPaginationError("Missing URL in pagination link.")
            url = self._url(target, base=response.url)
        raise GitHubPaginationError("GitHub pagination exceeded max_pages.")

    def _url(
        self,
        path: str,
        params: QueryParams | None = None,
        *,
        base: httpx.URL | None = None,
    ) -> httpx.URL:
        try:
            url = (base or httpx.URL(self.BASE_URL)).join(path)
        except (httpx.InvalidURL, ValueError):
            raise GitHubConfigurationError("Invalid GitHub API URL.") from None
        if (
            url.scheme != "https"
            or url.host != "api.github.com"
            or url.port not in (None, 443)
            or url.userinfo
            or url.fragment
        ):
            raise GitHubConfigurationError(
                "Only HTTPS api.github.com URLs are allowed."
            )
        return url.copy_merge_params(params) if params else url

    @staticmethod
    def _decode(response: httpx.Response) -> Payload:
        if response.status_code == 204:
            return None
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            raise GitHubResponseError("GitHub returned invalid JSON.") from None
        if not isinstance(payload, (dict, list)):
            raise GitHubResponseError("Expected a JSON object or array.")
        return payload

    def _observe_rate_limit(self, response: httpx.Response) -> None:
        now = self._clock()
        self.rate_limit = RateLimit.from_headers(response.headers, now=now)
        delay = self.rate_limit.retry_after or 0.0
        remaining = self.rate_limit.remaining
        if remaining is not None and remaining <= self._threshold:
            logger.warning(
                "github.rate_limit.low",
                extra={"event": "github.rate_limit.low", "remaining": remaining},
            )
            reset_delay = self.rate_limit.reset_delay(now)
            if self.rate_limit.reset_at is None:
                reset_delay = 60.0 if remaining == 0 else 1.0
            delay = max(delay, reset_delay)
        if delay > 0:
            self._not_before = max(self._not_before, now + delay)
            self._rate_status = response.status_code

    def _wait_for_rate_limit(self) -> None:
        delay = max(0.0, self._not_before - self._clock())
        if delay > self._max_wait:
            raise GitHubRateLimitError(delay, self._rate_status)
        if delay:
            logger.warning(
                "github.rate_limit.wait",
                extra={"event": "github.rate_limit.wait", "delay_seconds": delay},
            )
            self._sleep(delay)

    def _is_rate_limited(self, response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code != 403:
            return False
        if self.rate_limit.remaining == 0 or self.rate_limit.retry_after is not None:
            return True
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            return False
        message = body.get("message", "") if isinstance(body, dict) else ""
        return isinstance(message, str) and any(
            marker in message.lower() for marker in ("rate limit", "abuse detection")
        )

    def _request(self, url: httpx.URL) -> httpx.Response:
        retries = 0
        redirects = 0
        while True:
            self._wait_for_rate_limit()
            try:
                response = self._http.get(url)
            except httpx.TransportError as exc:
                if retries == self._max_retries:
                    error_type = (
                        GitHubTimeoutError
                        if isinstance(exc, httpx.TimeoutException)
                        else GitHubTransportError
                    )
                    raise error_type(
                        "GitHub transport retry budget exhausted."
                    ) from None
                self._backoff(retries)
                retries += 1
                continue
            logger.info(
                "github.request",
                extra={
                    "event": "github.request",
                    "status_code": response.status_code,
                    "attempt": retries + 1,
                },
            )
            self._observe_rate_limit(response)
            if response.is_success:
                return response
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location or redirects >= 5:
                    raise GitHubResponseError("Invalid or excessive GitHub redirects.")
                url = self._url(location, base=response.url)
                redirects += 1
                continue
            if self._is_rate_limited(response):
                delay = self.rate_limit.retry_after
                if (
                    self.rate_limit.remaining == 0
                    and self.rate_limit.reset_at is not None
                ):
                    delay = max(
                        delay or 0.0, self.rate_limit.reset_delay(self._clock())
                    )
                if delay is None:
                    delay = 60.0 * 2**retries
                delay = max(delay, self._backoff_factor * 2**retries)
                self._not_before = max(self._not_before, self._clock() + delay)
                self._rate_status = response.status_code
                if retries == self._max_retries:
                    raise GitHubRateLimitError(
                        self._not_before - self._clock(), response.status_code
                    )
                self._log_retry(retries, delay)
                retries += 1
                continue
            if response.is_server_error and retries < self._max_retries:
                self._backoff(retries)
                retries += 1
                continue
            error_type = {
                401: GitHubAuthenticationError,
                403: GitHubForbiddenError,
                404: GitHubNotFoundError,
            }.get(response.status_code, GitHubHTTPError)
            raise error_type(response.status_code)

    def _backoff(self, retries: int) -> None:
        delay = min(self._backoff_factor * 2**retries, self._max_wait)
        self._log_retry(retries, delay)
        self._sleep(delay)

    @staticmethod
    def _log_retry(retries: int, delay: float) -> None:
        logger.warning(
            "github.retry",
            extra={
                "event": "github.retry",
                "retry": retries + 1,
                "delay_seconds": delay,
            },
        )
