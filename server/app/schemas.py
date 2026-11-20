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


# ------------------------------------------------------------------ clés API
class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("le nom ne peut pas être vide")
        return value


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    rate_limit_per_min: int
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    key: str


# ------------------------------------------------------------------ machines
class Hardware(BaseModel):
    os: str = Field(min_length=1, max_length=120)
    gpu_model: str | None = Field(default=None, max_length=200)
    vram_mb: int = Field(default=0, ge=0, le=10_000_000)
    cpu_cores: int = Field(ge=1, le=4096)
    ram_mb: int = Field(ge=1, le=100_000_000)
    disk_free_gb: int = Field(ge=0, le=10_000_000)


class MachineCreate(ApiKeyCreate):
    pass


class MachineOut(BaseModel):
    id: uuid.UUID
    name: str
    status: Literal["pending", "online", "offline", "suspended"]
    tier: Literal["A", "B", "C"] | None
    hardware: Hardware | None
    agent_version: str | None
    benchmark_tokens_per_sec: float | None
    reliability_score: float
    jobs_succeeded: int
    jobs_failed: int
    last_heartbeat_at: datetime | None
    suspended_reason: str | None
    created_at: datetime

    @classmethod
    def from_machine(cls, machine) -> "MachineOut":
        hardware = None
        if machine.os is not None:
            hardware = Hardware(
                os=machine.os,
                gpu_model=machine.gpu_model,
                vram_mb=machine.vram_mb or 0,
                cpu_cores=machine.cpu_cores,
                ram_mb=machine.ram_mb,
                disk_free_gb=machine.disk_free_gb,
            )
        return cls(
            id=machine.id,
            name=machine.name,
            status=machine.status,
            tier=machine.tier,
            hardware=hardware,
            agent_version=machine.agent_version,
            benchmark_tokens_per_sec=(
                float(machine.benchmark_tokens_per_sec)
                if machine.benchmark_tokens_per_sec is not None
                else None
            ),
            reliability_score=float(machine.reliability_score),
            jobs_succeeded=machine.jobs_succeeded,
            jobs_failed=machine.jobs_failed,
            last_heartbeat_at=machine.last_heartbeat_at,
            suspended_reason=machine.suspended_reason,
            created_at=machine.created_at,
        )


class MachineCreated(BaseModel):
    machine: MachineOut
    enrollment_token: str
    enrollment_expires_at: datetime


class EnrollRequest(BaseModel):
    enrollment_token: str = Field(min_length=10, max_length=200)
    agent_version: str = Field(min_length=1, max_length=40)
    hardware: Hardware


class EnrollResponse(BaseModel):
    machine_id: uuid.UUID
    agent_token: str
    websocket_url: str
    tier: Literal["A", "B", "C"] | None
    eligible: bool
    ineligibility_reason: str | None
