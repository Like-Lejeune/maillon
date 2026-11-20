"""Routes compatibles OpenAI, authentifiées par clé API.

/v1/chat/completions : sans flux pour l'instant (flux prévu le 24 novembre), et
sans débit de crédits (registre de facturation prévu le 23 novembre).
"""

import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_api_key_user, get_db
from app.dispatch import JobFailed
from app.errors import ApiError
from app.models import Model

router = APIRouter(prefix="/v1", tags=["Inférence"])


@router.get("/models")
async def list_models(
    _auth=Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Model).where(Model.is_active.is_(True)).order_by(Model.slug))
    return {
        "object": "list",
        "data": [
            {
                "id": model.slug,
                "object": "model",
                "created": int(model.created_at.timestamp()),
                "owned_by": "maillon",
                "context_length": model.context_length,
                "pricing": {
                    "input": model.price_input_per_mtok,
                    "output": model.price_output_per_mtok,
                },
            }
            for model in result.scalars()
        ],
    }


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=200_000)


class ChatCompletionRequest(BaseModel):
    # Les clients OpenAI envoient parfois d'autres paramètres : ils sont ignorés.
    model_config = ConfigDict(extra="ignore")

    model: str = Field(min_length=1, max_length=120)
    messages: list[ChatMessage] = Field(min_length=1, max_length=500)
    stream: bool = False
    max_tokens: int | None = Field(default=None, ge=1, le=32_768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    stop: str | list[str] | None = None
    seed: int | None = None

    @field_validator("stop")
    @classmethod
    def _stop_limit(cls, value):
        if isinstance(value, list) and len(value) > 4:
            raise ValueError("4 séquences d'arrêt au plus")
        return value


@router.post("/chat/completions")
async def create_chat_completion(
    payload: ChatCompletionRequest,
    request: Request,
    auth=Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user, api_key = auth
    if payload.stream:
        raise ApiError(
            400,
            "invalid_request_error",
            "Les réponses en flux (stream) arrivent avant le lancement ; envoyez stream: false.",
            code="stream_not_supported",
            param="stream",
        )
    model = (
        await db.execute(select(Model).where(Model.slug == payload.model, Model.is_active.is_(True)))
    ).scalar_one_or_none()
    if model is None:
        raise ApiError(
            404,
            "not_found_error",
            f"Modèle inconnu : {payload.model}. La liste est sur GET /v1/models.",
            code="model_not_found",
            param="model",
        )

    forwarded = payload.model_dump(exclude_none=True, exclude={"stream"})
    forwarded["stream"] = False
    job_id = (
        await db.execute(
            text(
                "INSERT INTO jobs (client_id, api_key_id, model_id, request, stream) "
                "VALUES (:client, :key, :model, CAST(:request AS jsonb), false) RETURNING id"
            ),
            {"client": user.id, "key": api_key.id, "model": model.id, "request": json.dumps(forwarded)},
        )
    ).scalar_one()
    await db.commit()

    dispatcher = request.app.state.dispatcher
    try:
        outcome = await dispatcher.run(job_id, model.slug, model.min_vram_mb, forwarded)
    except JobFailed as failure:
        raise ApiError(failure.status, failure.error_type, failure.message, code=failure.code)

    return {
        "id": f"chatcmpl-{job_id.hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model.slug,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": outcome.content},
                "finish_reason": outcome.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": outcome.prompt_tokens,
            "completion_tokens": outcome.completion_tokens,
            "total_tokens": outcome.prompt_tokens + outcome.completion_tokens,
        },
    }
