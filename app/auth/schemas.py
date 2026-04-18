"""Pydantic schemas for authentication request and response bodies."""

from pydantic import BaseModel, EmailStr, Field

ROLE_PATTERN = r"^(super_admin|admin|editor|user|viewer)$"


class LoginRequest(BaseModel):
    email: EmailStr = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    role: str = Field(default="user", pattern=ROLE_PATTERN)


class UpdateUserRoleRequest(BaseModel):
    role: str = Field(pattern=ROLE_PATTERN)


class UserResponse(BaseModel):
    id: int
    email: str
    role: str = Field(pattern=ROLE_PATTERN)
    created_at: str
    last_login_at: str | None = None
    failed_attempts: int = 0
    locked_until: str | None = None


class UserMutationResponse(BaseModel):
    message: str
    user: UserResponse


class MetricOverrideRequest(BaseModel):
    value: float


class MetricResponse(BaseModel):
    metric_name: str
    base_value: float | int | None
    override_value: float | None = None
    value: float | int | None
    updated_at: str | None = None


class MessageResponse(BaseModel):
    message: str
