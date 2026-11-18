"""Routes compatibles OpenAI, authentifiées par clé API.

Pour l'instant : la liste des modèles. /v1/chat/completions arrive avec
l'ordonnanceur (planning, 23 novembre).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_api_key_user, get_db
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
