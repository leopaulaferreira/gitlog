"""GitHub's mixed issue stream must never conflate issue IDs and PR IDs."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from ingestion.client import GitHubClient
from ingestion.client.exceptions import GitHubPaginationError
from ingestion.extractors.issues import IssueExtractor, issue_page
from ingestion.extractors.pull_requests import PullRequestExtractor
from ingestion.loaders.raw_loader import RawLoader
from ingestion.models.issues import Issue, PullRequest


@pytest.mark.parametrize("state", ["open", "closed"])
def test_issue_fields_and_nullable_author(issue_payload, state):
    issue_payload.update(
        state=state,
        user=None,
        closed_at=None if state == "open" else "2026-09-27T12:00:00Z",
    )
    original = json.dumps(issue_payload)
    model = Issue.model_validate(issue_payload)
    assert model.state == state and model.author_login is None
    assert model.comments_count == 3 and model.number == 1
    assert json.dumps(issue_payload) == original


@pytest.mark.parametrize(
    "state,merged", [("open", False), ("closed", False), ("closed", True)]
)
def test_pr_open_closed_and_merged(pull_request_payload, state, merged):
    pull_request_payload.update(
        state=state, merged_at="2026-09-27T12:00:00Z" if merged else None
    )
    model = PullRequest.model_validate(pull_request_payload)
    assert model.id == 9002 and model.number == 2
    assert (model.merged_at is not None) == merged
    # GitHub can return a test merge SHA for a PR which is still open.
    assert model.merge_commit_sha == "a" * 40


@pytest.mark.parametrize("marker", [None, {}, {"url": "https://example.test/pr"}])
def test_presence_of_marker_excludes_pr_even_if_null(issue_payload, marker):
    issue_payload["pull_request"] = marker
    assert issue_page([issue_payload], pull_requests=False) == []
    assert issue_page([issue_payload], pull_requests=True) == [issue_payload]
    with pytest.raises(ValidationError):
        Issue.model_validate(issue_payload)


def test_issue_identity_cannot_be_used_for_pull_request(
    pull_request_issue_payload, pull_request_payload
):
    payload = {**pull_request_payload, **pull_request_issue_payload}
    with pytest.raises(ValidationError):
        PullRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("id", "1"),
        ("number", 0),
        ("state", "merged"),
        ("comments", -1),
        ("created_at", "2026-01-01T00:00:00"),
        ("user", {}),
    ],
)
def test_issue_validation(issue_payload, field, value):
    issue_payload[field] = value
    with pytest.raises(ValidationError):
        Issue.model_validate(issue_payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("draft", "false"),
        ("merge_commit_sha", "not-a-sha"),
        ("merged_at", "2026-09-27T12:00:00Z"),
    ],
)
def test_pr_validation(pull_request_payload, field, value):
    pull_request_payload[field] = value
    with pytest.raises(ValidationError):
        PullRequest.model_validate(pull_request_payload)


def test_draft_pr_with_deleted_author(pull_request_payload):
    pull_request_payload.update(draft=True, user=None, merge_commit_sha=None)
    model = PullRequest.model_validate(pull_request_payload)
    assert model.draft is True and model.author_login is None
    assert model.merged_at is None


@pytest.mark.parametrize("payload", [{}, [None], [1]])
def test_malformed_issue_page(payload):
    with pytest.raises(GitHubPaginationError):
        issue_page(payload, pull_requests=False)


@pytest.mark.parametrize("extractor_type", [IssueExtractor, PullRequestExtractor])
def test_paginated_discovery_uses_all_states_updated_since_and_exact_links(
    extractor_type, issue_payload, pull_request_issue_payload
):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.params["state"] == "all"
        assert request.url.params["sort"] == "created"
        assert request.url.params["direction"] == "asc"
        assert request.url.params["since"] == "2026-09-27T11:55:00Z"
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=[issue_payload],
                headers={"Link": f'<{request.url}&page=2>; rel="next"'},
            )
        assert request.url.params["page"] == "2"
        return httpx.Response(200, json=[pull_request_issue_payload])

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        extractor = extractor_type(client, per_page=1)
        pages = list(
            extractor.pages(
                "octocat/Hello-World", datetime(2026, 9, 27, 11, 55, tzinfo=UTC)
            )
        )
        selected = [item for page in pages for item in extractor.selected(page)]
    assert len(pages) == 2
    assert [p["id"] for p in selected] == (
        [1001] if extractor_type is IssueExtractor else [1002]
    )


def test_pr_detail_uses_constructed_endpoint_not_payload_url(pull_request_payload):
    def handler(request):
        assert request.url.path == "/repos/octocat/Hello-World/pulls/2"
        return httpx.Response(200, json=pull_request_payload)

    with GitHubClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            PullRequestExtractor(client).detail("octocat/Hello-World", 2)["id"] == 9002
        )


@pytest.mark.parametrize("entity", ["issues", "pull_requests"])
def test_raw_storage_keeps_mixed_pages_without_mutation(
    tmp_path, entity, issue_payload, pull_request_issue_payload
):
    original = [issue_payload, pull_request_issue_payload]
    loader = RawLoader(tmp_path)
    run_id = uuid4()
    instant = datetime.now(UTC)
    path = loader.save_document(entity, "a/b", run_id, "page-000001", original, instant)
    assert json.loads(path.read_text()) == original
    assert entity in path.parts
    with pytest.raises(FileExistsError):
        loader.save_document(entity, "a/b", run_id, "page-000001", [], instant)
    with pytest.raises(ValueError):
        loader.save_document("../escape", "a/b", run_id, "page-1", [], instant)
