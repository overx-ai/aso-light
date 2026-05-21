from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class PATCreateRequest(BaseModel):
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    ]


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
