"""Shared issue/PR fields, with strict separation of their GitHub identities."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from ingestion.models.commits import SHA
from ingestion.models.github_models import Count, Identifier, Text, nonblank


class IssueFields(BaseModel):
    id: Identifier
    number: Identifier
    title: Text
    state: Literal["open", "closed"]
    author_login: str | None = Field(validation_alias="user")
    created_at: AwareDatetime
    updated_at: AwareDatetime
    closed_at: AwareDatetime | None

    @model_validator(mode="after")
    def valid_timeline(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Update precedes creation.")
        if self.closed_at is not None and self.closed_at < self.created_at:
            raise ValueError("Closure precedes creation.")
        return self

    @field_validator("author_login", mode="before")
    @classmethod
    def github_login(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("login"), str):
            raise ValueError("Expected a GitHub user or null.")
        return nonblank(value["login"])


class Issue(IssueFields):
    comments_count: Count = Field(validation_alias="comments")

    @model_validator(mode="before")
    @classmethod
    def exclude_pull_requests(cls, value: object) -> object:
        if isinstance(value, dict) and "pull_request" in value:
            raise ValueError("Pull requests must not be loaded as issues.")
        return value


class PullRequest(IssueFields):
    merged_at: AwareDatetime | None
    merge_commit_sha: SHA | None
    draft: Annotated[bool, Field(strict=True)]

    @model_validator(mode="before")
    @classmethod
    def require_pull_response(cls, value: object) -> object:
        if isinstance(value, dict) and "pull_request" in value:
            raise ValueError("Use the pull request response, not its issue identity.")
        return value

    @model_validator(mode="after")
    def merged_is_closed(self) -> Self:
        if self.merged_at is not None and self.state != "closed":
            raise ValueError("A merged pull request must be closed.")
        if self.merged_at is not None and self.merged_at < self.created_at:
            raise ValueError("Merge precedes creation.")
        return self
