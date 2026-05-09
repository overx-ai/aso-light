from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PATCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Name must not be blank")
        return stripped


class PATCreateResponse(BaseModel):
    """Response from POST /auth/tokens.

    `token` is the plaintext bearer string and is returned exactly once.
    Subsequent listings never include it.
    """

    id: int
    name: str
    token: str
    created_at: datetime


class PATListItem(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
