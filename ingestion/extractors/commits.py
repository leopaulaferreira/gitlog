"""Pinned commit history and paginated graph differences, using the shared client."""

from collections.abc import Iterator
from urllib.parse import quote

from ingestion.client import GitHubClient
from ingestion.client.exceptions import GitHubNotFoundError, GitHubPaginationError
from ingestion.client.github_client import Payload
from ingestion.config import validate_repository_name


def page_commits(mode: str, payload: Payload) -> list[dict]:
    if mode == "compare":
        if not isinstance(payload, dict) or payload.get("status") not in (
            "ahead",
            "behind",
            "diverged",
            "identical",
        ):
            raise GitHubPaginationError("Invalid comparison response.")
        if payload["status"] != "ahead":
            return []  # The extractor will follow with a complete history scan.
        items = payload.get("commits")
    else:
        items = payload
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise GitHubPaginationError("Expected a commit array.")
    return items


class CommitExtractor:
    def __init__(self, client: GitHubClient, *, per_page: int = 100) -> None:
        self.client = client
        self.per_page = per_page

    def repository(self, name: str) -> Payload:
        validate_repository_name(name)
        return self.client.get(f"/repos/{name}")

    def reference(self, name: str, branch: str) -> Payload:
        return self.client.get(f"/repos/{name}/git/ref/heads/{quote(branch, safe='/')}")

    def pages(
        self, name: str, head: str, base: str | None
    ) -> Iterator[tuple[str, Payload]]:
        if base == head:
            return
        if base is not None:
            comparison = self.client.get_pages(
                f"/repos/{name}/compare/{base}...{head}", per_page=self.per_page
            )
            try:
                first = next(comparison)
            except GitHubNotFoundError:
                # A force-pushed base may no longer be accessible.
                first = None
            if first is not None:
                yield "compare", first
                if isinstance(first, dict) and first.get("status") == "ahead":
                    for page in comparison:
                        yield "compare", page
                    return
        for page in self.client.get_pages(
            f"/repos/{name}/commits", params={"sha": head}, per_page=self.per_page
        ):
            yield "list", page
