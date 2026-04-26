from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AppResponse(BaseModel):
    id: int
    name: str
    bundle_id: str
    platform: str
    icon_url: str | None
    asc_app_id: str
    credential_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AppSyncResponse(BaseModel):
    synced: int
    apps: list[AppResponse]
