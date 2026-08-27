from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

# Email is matched byte-for-byte against users.email (no COLLATE NOCASE), so a
# single capital from autofill/autocapitalize would miss the row and surface as
# "invalid password". Normalize once, here, for every auth path — login must
# lowercase the same way register does or the two disagree.
NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]


class LoginRequest(BaseModel):
    email: NormalizedEmail
    password: str


class RegisterRequest(BaseModel):
    email: NormalizedEmail
    # bcrypt silently truncates input past 72 bytes; cap the length so two long
    # passwords sharing a 72-byte prefix can't be set and verify as equal.
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
