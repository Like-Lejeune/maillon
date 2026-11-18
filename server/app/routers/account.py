"""Solde, mouvements et consommation du compte connecté."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_user, get_db, require_role
from app.errors import ApiError
from app.models import Balance, User

router = APIRouter(prefix="/api", tags=["Client", "Fournisseur"])

MAX_USAGE_DAYS = 366


class BalanceOut(BaseModel):
    available: int
    pending: int


class LedgerEntryOut(BaseModel):
    id: int
    type: str
    amount: int
    balance_after: int
    job_id: str | None
    note: str | None
    created_at: datetime


class LedgerPage(BaseModel):
    data: list[LedgerEntryOut]
    next_cursor: str | None


class UsageRow(BaseModel):
    date: date
    model: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost: int


@router.get("/balance", response_model=BalanceOut)
async def get_balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BalanceOut:
    balance = await db.get(Balance, user.id)
    if balance is None:
        return BalanceOut(available=0, pending=0)
    return BalanceOut(available=balance.available, pending=balance.pending)


@router.get("/ledger", response_model=LedgerPage)
async def list_ledger(
    cursor: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LedgerPage:
    before = None
    if cursor is not None:
        if not cursor.isdigit():
            raise ApiError(400, "invalid_request_error", "Curseur invalide.", param="cursor")
        before = int(cursor)
    rows = (
        await db.execute(
            text(
                """
                SELECT id, type::text AS type, amount, balance_after, job_id::text AS job_id,
                       note, created_at
                FROM ledger_entries
                WHERE account = 'user' AND user_id = :user_id
                  AND (CAST(:before AS bigint) IS NULL OR id < :before)
                ORDER BY id DESC
                LIMIT :limit
                """
            ),
            {"user_id": user.id, "before": before, "limit": limit + 1},
        )
    ).mappings().all()
    has_more = len(rows) > limit
    entries = [LedgerEntryOut(**row) for row in rows[:limit]]
    return LedgerPage(
        data=entries,
        next_cursor=str(entries[-1].id) if has_more and entries else None,
    )


@router.get("/usage", response_model=list[UsageRow])
async def get_usage(
    from_: date = Query(alias="from"),
    to: date = Query(),
    user: User = Depends(require_role("client")),
    db: AsyncSession = Depends(get_db),
) -> list[UsageRow]:
    """Consommation par jour (UTC) et par modèle, tâches réussies seulement, bornes incluses."""
    if to < from_:
        raise ApiError(400, "invalid_request_error", "« to » doit suivre « from ».", param="to")
    if (to - from_).days >= MAX_USAGE_DAYS:
        raise ApiError(
            400, "invalid_request_error", f"Période limitée à {MAX_USAGE_DAYS} jours.", param="from"
        )
    start = datetime(from_.year, from_.month, from_.day, tzinfo=timezone.utc)
    end = datetime(to.year, to.month, to.day, tzinfo=timezone.utc) + timedelta(days=1)
    rows = (
        await db.execute(
            text(
                """
                SELECT (j.created_at AT TIME ZONE 'UTC')::date AS date,
                       m.slug AS model,
                       count(*) AS requests,
                       coalesce(sum(j.prompt_tokens), 0) AS prompt_tokens,
                       coalesce(sum(j.completion_tokens), 0) AS completion_tokens,
                       coalesce(sum(j.cost), 0) AS cost
                FROM jobs j
                JOIN models m ON m.id = j.model_id
                WHERE j.client_id = :user_id AND j.status = 'succeeded'
                  AND j.created_at >= :start AND j.created_at < :end
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            ),
            {"user_id": user.id, "start": start, "end": end},
        )
    ).mappings().all()
    return [UsageRow(**row) for row in rows]
