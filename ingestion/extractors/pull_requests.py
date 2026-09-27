"""Hydrate PRs discovered by updated issues using their true pull request IDs."""

from pydantic import TypeAdapter

from ingestion.client.github_client import Payload
from ingestion.config import validate_repository_name
from ingestion.extractors.issues import IssueExtractor
from ingestion.models.github_models import Identifier
from ingestion.models.issues import PullRequest


class PullRequestExtractor(IssueExtractor):
    entity = "pull_requests"
    model = PullRequest

    def detail(self, repository: str, number: int) -> Payload:
        validate_repository_name(repository)
        number = TypeAdapter(Identifier).validate_python(number)
        return self.client.get(f"/repos/{repository}/pulls/{number}")
