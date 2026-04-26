from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CredentialResponse(BaseModel):
    id: int
    name: str
    issuer_id: str
    key_id: str
    created_at: datetime
    apps_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class CredentialTestResponse(BaseModel):
    success: bool
    message: str
    apps_count: int | None = None
