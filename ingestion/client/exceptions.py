"""Public errors deliberately exclude response bodies, URLs and credentials."""


class GitHubError(Exception):
    """Base error for the GitLog GitHub client."""


class GitHubConfigurationError(GitHubError):
    """Missing credentials, invalid configuration or unsafe URL."""


class GitHubHTTPError(GitHubError):
    """An unsuccessful HTTP response, identified by its status code."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"GitHub request failed with HTTP {status_code}.")


class GitHubAuthenticationError(GitHubHTTPError):
    """HTTP 401: invalid or expired authentication."""


class GitHubForbiddenError(GitHubHTTPError):
    """HTTP 403 without evidence of rate limiting."""


class GitHubNotFoundError(GitHubHTTPError):
    """HTTP 404: resource absent or inaccessible with the current credentials."""


class GitHubRateLimitError(GitHubError):
    """Retry budget exhausted or required wait exceeds the configured limit."""

    def __init__(self, retry_after: float, status_code: int | None = None) -> None:
        self.retry_after = retry_after
        self.status_code = status_code
        super().__init__(
            "GitHub rate limit prevents another request within the configured budget."
        )


class GitHubTransportError(GitHubError):
    """Network or protocol failure after bounded retries."""


class GitHubTimeoutError(GitHubTransportError):
    """Request timed out after bounded retries."""


class GitHubResponseError(GitHubError):
    """Unexpected JSON response or invalid redirect."""


class GitHubPaginationError(GitHubResponseError):
    """Unexpected page shape, repeated page or page budget exceeded."""
