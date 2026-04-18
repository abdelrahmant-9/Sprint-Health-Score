"""Pydantic schemas for authentication request and response bodies."""

from pydantic import BaseModel, EmailStr, Field


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
    email: str = Field(min_length=1)
    password: str = Field(min_length=6)
    role: str = Field(default="user", pattern=r"^(admin|editor|user|viewer)$")


class MessageResponse(BaseModel):
    message: str
