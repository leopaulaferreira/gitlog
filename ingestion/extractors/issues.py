"""Shared updated-issue discovery; PRs are selected by marker, never by URL."""

from collections.abc import Iterator
from datetime import UTC, datetime

from ingestion.client import GitHubClient
from ingestion.client.exceptions import GitHubPaginationError
from ingestion.client.github_client import Payload
from ingestion.config import validate_repository_name
from ingestion.models.issues import Issue


def issue_page(payload: Payload, *, pull_requests: bool) -> list[dict]:
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise GitHubPaginationError("Expected an issue array.")
    return [item for item in payload if ("pull_request" in item) == pull_requests]


class IssueExtractor:
    entity = "issues"
    model = Issue

    def __init__(self, client: GitHubClient, *, per_page: int = 100) -> None:
        self.client, self.per_page = client, per_page

    def pages(self, repository: str, since: datetime | None) -> Iterator[Payload]:
        validate_repository_name(repository)
        # since filters updates; creation order keeps an edited item from moving
        # to another page while we walk GitHub's mutable offset pagination.
        params = {"state": "all", "sort": "created", "direction": "asc"}
        if since is not None:
            if since.tzinfo is None:
                raise ValueError("Checkpoint time must be timezone aware.")
            params["since"] = (
                since.astimezone(UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        yield from self.client.get_pages(
            f"/repos/{repository}/issues", params=params, per_page=self.per_page
        )

    def selected(self, payload: Payload) -> list[dict]:
        return issue_page(payload, pull_requests=self.entity == "pull_requests")
