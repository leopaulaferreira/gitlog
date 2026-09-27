"""Commit mapping, immutable raw pages and graph pagination contracts."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from ingestion.client import GitHubClient
from ingestion.client.exceptions import GitHubPaginationError
from ingestion.extractors.commits import CommitExtractor, page_commits
from ingestion.loaders.raw_loader import RawLoader
from ingestion.models.commits import Commit


def test_commit_maps_unlinked_author_and_parents_without_mutation(commit_payload):
    original = json.dumps(commit_payload)
    values = Commit.model_validate(commit_payload).database_values()
    assert values["author_login"] is None
    assert values["author_name"] == "Test Author"
    assert values["committer_login"] == "octocat"
    assert values["parent_shas"] == ["b" * 40]
    assert values["committed_at"] == datetime(2020, 1, 1, 2, tzinfo=UTC)
    assert json.dumps(commit_payload) == original


def test_commit_allows_missing_git_identity_and_root_commit(commit_payload):
    commit_payload["commit"].update(author=None, committer=None)
    commit_payload.update(author=None, committer=None, parents=[])
    values = Commit.model_validate(commit_payload).database_values()
    assert values["committed_at"] is None
    assert values["author_email"] is None
    assert values["parent_shas"] == []


@pytest.mark.parametrize("sha", ["", "a" * 39, "g" * 40, "../secret", 123])
def test_commit_rejects_invalid_sha(commit_payload, sha):
    commit_payload["sha"] = sha
    with pytest.raises(ValidationError):
        Commit.model_validate(commit_payload)


def test_commit_rejects_naive_timestamp(commit_payload):
    commit_payload["commit"]["committer"]["date"] = "2020-01-01T00:00:00"
    with pytest.raises(ValidationError):
        Commit.model_validate(commit_payload)


def test_raw_commit_pages_are_immutable_and_preserve_envelopes(
    tmp_path, commit_payload
):
    raw = RawLoader(tmp_path)
    run = uuid4()
    payload = {"commits": [commit_payload], "unknown": "ação"}
    path = raw.save_commit_document(
        "octocat/Hello-World", run, "page-000001", payload, datetime.now(UTC)
    )
    assert path.is_absolute()
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    with pytest.raises(FileExistsError):
        raw.save_commit_document(
            "octocat/Hello-World", run, "page-000001", {}, datetime.now(UTC)
        )
    with pytest.raises(ValueError):
        raw.save_commit_document("a/b", run, "../escape", {}, datetime.now(UTC))
    assert not list(tmp_path.rglob("*.tmp"))


def test_incremental_compare_follows_link_and_keeps_entire_response(commit_payload):
    requests = []
    first = {
        "status": "ahead",
        "total_commits": 2,
        "commits": [commit_payload],
        "files": [{"filename": "raw-metadata"}],
    }
    second = {
        "status": "ahead",
        "total_commits": 2,
        "commits": [{**commit_payload, "sha": "c" * 40}],
    }

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.url.params["per_page"] == "1"
            return httpx.Response(
                200, json=first, headers={"Link": f'<{request.url}&page=2>; rel="next"'}
            )
        assert request.url.params["page"] == "2"
        return httpx.Response(200, json=second)

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        pages = list(
            CommitExtractor(client, per_page=1).pages("a/b", "c" * 40, "b" * 40)
        )
    assert pages == [("compare", first), ("compare", second)]
    assert len(requests) == 2


@pytest.mark.parametrize("status", ["diverged", "behind", "identical", "missing"])
def test_unusable_base_falls_back_to_pinned_full_history(status, commit_payload):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if "/compare/" in request.url.path:
            if status == "missing":
                return httpx.Response(404)
            return httpx.Response(200, json={"status": status, "commits": []})
        assert request.url.params["sha"] == "a" * 40
        return httpx.Response(200, json=[commit_payload])

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        pages = list(CommitExtractor(client).pages("a/b", "a" * 40, "b" * 40))
    assert pages[-1] == ("list", [commit_payload])
    assert len(paths) == 2


def test_unchanged_head_never_requests_history():
    def handler(request):
        pytest.fail("Unchanged head must not request commit pages.")

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        assert list(CommitExtractor(client).pages("a/b", "a" * 40, "a" * 40)) == []


@pytest.mark.parametrize(
    "mode,payload",
    [
        ("list", {}),
        ("list", [None]),
        ("compare", {"status": "unknown"}),
        ("compare", {"status": "ahead", "commits": {}}),
    ],
)
def test_malformed_pages_fail(mode, payload):
    with pytest.raises(GitHubPaginationError):
        page_commits(mode, payload)


def test_raw_page_failure_does_not_publish_partial_json(tmp_path: Path, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("ingestion.loaders.raw_loader.os.fsync", fail)
    with pytest.raises(OSError):
        RawLoader(tmp_path).save_commit_document(
            "a/b", uuid4(), "page-000001", [], datetime.now(UTC)
        )
    assert not list(tmp_path.rglob("*.json"))
    assert not list(tmp_path.rglob("*.tmp"))
