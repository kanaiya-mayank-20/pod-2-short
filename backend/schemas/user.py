"""User request/response shapes."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from models.user import UserRole


class UserCreate(BaseModel):
    """Registration body. The password is never stored, only its hash."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=200)


class UserRead(BaseModel):
    """Safe to return: no password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    name: str
    role: UserRole
    is_active: bool
    created_at: datetime
