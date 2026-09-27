from ingestion.client import GitHubClient
from ingestion.client.github_client import Payload
from ingestion.config import validate_repository_name


class RepositoryExtractor:
    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def extract(self, repository: str) -> Payload:
        name = validate_repository_name(repository)
        return self.client.get(f"/repos/{name}")
