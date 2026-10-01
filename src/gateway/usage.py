"""Usage ledger: one row per completed request, with tokens, cost and latency."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from gateway.config import ModelInfo
from gateway.db import Base
from gateway.schemas import ModelUsage, UsageSummary


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    # NUMERIC, not float: summing many tiny float costs drifts, and this is money.
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    latency_ms: Mapped[int]


async def record_usage(
    session: AsyncSession,
    model: ModelInfo,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
) -> None:
    session.add(
        UsageRecord(
            model=model.id,
            provider=model.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=model.cost(prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
        )
    )
    await session.commit()


async def usage_summary(session: AsyncSession) -> UsageSummary:
    query = (
        select(
            UsageRecord.model,
            func.count(),
            func.sum(UsageRecord.prompt_tokens),
            func.sum(UsageRecord.completion_tokens),
            func.sum(UsageRecord.cost_usd),
        )
        .group_by(UsageRecord.model)
        .order_by(UsageRecord.model)
    )
    by_model = [
        ModelUsage(
            model=model,
            requests=requests,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
        )
        for model, requests, prompt_tokens, completion_tokens, cost in await session.execute(query)
    ]
    return UsageSummary(
        requests=sum(u.requests for u in by_model),
        prompt_tokens=sum(u.prompt_tokens for u in by_model),
        completion_tokens=sum(u.completion_tokens for u in by_model),
        cost_usd=sum((u.cost_usd for u in by_model), Decimal(0)),
        by_model=by_model,
    )
