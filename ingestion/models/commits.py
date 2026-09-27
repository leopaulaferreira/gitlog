"""Commit identity is repository ID + SHA; Git authors need not have accounts."""

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field

type SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class GitIdentity(BaseModel):
    name: str | None = None
    email: str | None = None
    date: AwareDatetime | None = None


class GitUser(BaseModel):
    login: str


class CommitMetadata(BaseModel):
    message: str
    author: GitIdentity | None
    committer: GitIdentity | None


class Parent(BaseModel):
    sha: SHA


class Commit(BaseModel):
    sha: SHA
    commit: CommitMetadata
    author: GitUser | None
    committer: GitUser | None
    parents: list[Parent]

    def database_values(self) -> dict:
        author, committer = self.commit.author, self.commit.committer
        return {
            "sha": self.sha,
            "message": self.commit.message,
            "author_name": author.name if author else None,
            "author_email": author.email if author else None,
            "authored_at": author.date if author else None,
            "committer_name": committer.name if committer else None,
            "committer_email": committer.email if committer else None,
            "committed_at": committer.date if committer else None,
            "author_login": self.author.login if self.author else None,
            "committer_login": self.committer.login if self.committer else None,
            "parent_shas": [parent.sha for parent in self.parents],
        }


class ReferenceObject(BaseModel):
    sha: SHA
    type: str


class Reference(BaseModel):
    ref: str
    object: ReferenceObject
