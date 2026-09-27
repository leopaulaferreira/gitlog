"""Isolate configuration and block real HTTP in unit and integration tests."""

import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture(autouse=True)
def isolate_pipeline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Integration connections use GITLOG_TEST_CONFIG from the isolated runner.
    for name in (
        "GITLOG_REPOSITORIES",
        "GITLOG_RAW_DIR",
        "GITLOG_DB_USER",
        "GITLOG_DB_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolate_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-not-a-real-secret")

    def deny_network(*args: object, **kwargs: object) -> None:
        pytest.fail("Real HTTP is forbidden in tests; use MockTransport.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_network)


@pytest.fixture
def repository_payload() -> dict:
    # Fixtures are never used by the CLI or production ingestion code.
    return json.loads(
        (Path(__file__).parent / "fixtures" / "repository.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture
def commit_payload() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "commit.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def issue_payload() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "issue.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def pull_request_payload() -> dict:
    return json.loads(
        (Path(__file__).parent / "fixtures" / "pull_request.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture
def pull_request_issue_payload(issue_payload: dict) -> dict:
    return {
        **issue_payload,
        "id": 1002,
        "number": 2,
        "pull_request": {
            "url": "https://api.github.com/repos/octocat/Hello-World/pulls/2"
        },
    }
