"""Abuse and cost guardrails for `POST /ask` (D-020): per-visitor daily limit, monthly spend cap.

Visitor: `HMAC(daily_key, client IP)` with `daily_key = HMAC(secret, day)`. The raw IP is never
stored; a hash cannot be linked to the same visitor on another day; counters of past days are
deleted on every write (CLAUDE.md, rule 5).

Spend: tokens of the current month in `query_logs` (prompt, completion and query-embedding
tokens) times the configured list prices. Prices are in USD and counted 1:1 as EUR, which
overestimates spend while 1 USD is worth less than 1 EUR. Requests refused by a guardrail call
no model and cost nothing.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import Engine, Integer, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.models import QueryLog, UsageCounter

Outcome = Literal["ok", "rate_limited", "spend_cap"]


@dataclass(frozen=True)
class Prices:
    chat_input_per_m: float
    chat_output_per_m: float
    embed_input_per_m: float

    def cost(self, prompt: int, completion: int, embedding: int) -> float:
        return (
            prompt * self.chat_input_per_m
            + completion * self.chat_output_per_m
            + embedding * self.embed_input_per_m
        ) / 1_000_000


@dataclass(frozen=True)
class Admission:
    outcome: Outcome
    retry_after_s: int = 0


class Guard(Protocol):
    def admit(self, client_ip: str) -> Admission: ...


def visitor_hash(client_ip: str, day: date, secret: bytes) -> str:
    daily_key = hmac.new(secret, day.isoformat().encode(), hashlib.sha256).digest()
    return hmac.new(daily_key, client_ip.encode(), hashlib.sha256).hexdigest()


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def seconds_to_next_day(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


def seconds_to_next_month(now: datetime) -> int:
    start = month_start(now)
    following = (start + timedelta(days=32)).replace(day=1)
    return int((following - now).total_seconds())


def month_spend(session: Session, now: datetime, prices: Prices) -> float:
    embedding = cast(QueryLog.eval_flags["embedding_tokens"].astext, Integer)
    row = session.execute(
        select(
            func.coalesce(func.sum(QueryLog.prompt_tokens), 0),
            func.coalesce(func.sum(QueryLog.completion_tokens), 0),
            func.coalesce(func.sum(embedding), 0),
        ).where(QueryLog.created_at >= month_start(now))
    ).one()
    return prices.cost(int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


@dataclass
class DatabaseGuard:
    engine: Engine
    daily_limit: int
    monthly_cap: float
    prices: Prices
    secret: bytes

    def admit(self, client_ip: str, now: datetime | None = None) -> Admission:
        now = now or datetime.now(UTC)
        today = now.date()
        with Session(self.engine) as session, session.begin():
            if month_spend(session, now, self.prices) >= self.monthly_cap:
                return Admission("spend_cap", seconds_to_next_month(now))
            session.execute(delete(UsageCounter).where(UsageCounter.day < today))
            count = session.execute(
                insert(UsageCounter)
                .values(day=today, visitor=visitor_hash(client_ip, today, self.secret), questions=1)
                .on_conflict_do_update(
                    index_elements=[UsageCounter.day, UsageCounter.visitor],
                    set_={"questions": UsageCounter.questions + 1},
                )
                .returning(UsageCounter.questions)
            ).scalar_one()
        if count > self.daily_limit:
            return Admission("rate_limited", seconds_to_next_day(now))
        return Admission("ok")
