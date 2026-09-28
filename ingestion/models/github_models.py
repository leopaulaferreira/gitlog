"""Map repository metadata only after its original payload has been preserved."""

from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AliasPath,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from ingestion.config import validate_repository_name

type Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
type Identifier = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Required text must not be blank.")
    return value


type Text = Annotated[str, Field(min_length=1), AfterValidator(nonblank)]


class Repository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Identifier
    node_id: Text
    name: Text
    full_name: Annotated[str, Field(min_length=3)]
    owner: Text = Field(validation_alias=AliasPath("owner", "login"))
    description: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    pushed_at: AwareDatetime | None
    language: str | None
    stars: Count = Field(validation_alias="stargazers_count")
    forks: Count = Field(validation_alias="forks_count")
    watchers: Count = Field(validation_alias="subscribers_count")
    open_issues: Count = Field(validation_alias="open_issues_count")
    default_branch: Text
    archived: Annotated[bool, Field(strict=True)]
    visibility: Literal["public", "private", "internal"]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Repository update precedes creation.")
        validate_repository_name(self.full_name)
        if self.full_name.lower() != f"{self.owner}/{self.name}".lower():
            raise ValueError("Inconsistent repository identity.")
        return self
