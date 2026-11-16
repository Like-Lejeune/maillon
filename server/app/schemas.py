"""Schémas d'entrée et de sortie, alignés sur docs/openapi.yaml."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    # Argon2 accepte de longs mots de passe ; la borne haute évite d'en faire
    # un moyen de saturer le serveur.
    password: str = Field(min_length=10, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    # Un compte admin ne se crée jamais par l'API (voir app/cli.py).
    role: Literal["client", "provider"]

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("le nom ne peut pas être vide")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    name: str
    role: Literal["client", "provider", "admin"]
    status: Literal["active", "suspended"]
    created_at: datetime


class SessionOut(BaseModel):
    token: str
    expires_at: datetime
    user: UserOut
