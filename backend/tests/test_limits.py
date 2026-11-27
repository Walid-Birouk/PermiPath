"""Daily limit and spend cap. Token counts and prices are fictive."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.limits import (
    DatabaseGuard,
    Prices,
    month_spend,
    seconds_to_next_day,
    seconds_to_next_month,
    visitor_hash,
)
from db.models import QueryLog, UsageCounter

PRICES = Prices(chat_input_per_m=2.0, chat_output_per_m=4.0, embed_input_per_m=1.0)
NOW = datetime(2026, 11, 27, 23, 0, tzinfo=UTC)


def test_visitor_hash_is_stable_within_a_day_and_unlinkable_across_days() -> None:
    a = visitor_hash("203.0.113.7", date(2026, 11, 27), b"s")
    assert a == visitor_hash("203.0.113.7", date(2026, 11, 27), b"s")
    assert a != visitor_hash("203.0.113.7", date(2026, 11, 28), b"s")
    assert a != visitor_hash("203.0.113.8", date(2026, 11, 27), b"s")
    assert a != visitor_hash("203.0.113.7", date(2026, 11, 27), b"other secret")
    assert "203.0.113.7" not in a


def test_prices_and_retry_delays() -> None:
    assert PRICES.cost(1_000_000, 500_000, 2_000_000) == pytest.approx(2.0 + 2.0 + 2.0)
    assert seconds_to_next_day(NOW) == 3600
    assert seconds_to_next_month(datetime(2026, 12, 31, 23, 0, tzinfo=UTC)) == 3600


pytestmark_db = pytest.mark.db


def _guard(engine: Engine, limit: int = 2, cap: float = 1.0) -> DatabaseGuard:
    return DatabaseGuard(engine, limit, cap, PRICES, b"test-secret")


@pytestmark_db
def test_daily_limit_counts_per_visitor_and_drops_past_days(migrated_engine: Engine) -> None:
    guard = _guard(migrated_engine)
    with Session(migrated_engine) as session, session.begin():
        session.add(UsageCounter(day=date(2026, 11, 26), visitor="old", questions=9))
    outcomes = [guard.admit("198.51.100.1", NOW).outcome for _ in range(3)]
    assert outcomes == ["ok", "ok", "rate_limited"]
    assert guard.admit("198.51.100.2", NOW).outcome == "ok"
    with Session(migrated_engine) as session:
        rows = session.execute(select(UsageCounter)).scalars().all()
        assert {r.day for r in rows} == {date(2026, 11, 27)}
        assert all("198.51.100" not in r.visitor for r in rows)


@pytestmark_db
def test_spend_cap_uses_this_months_tokens_only(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as session, session.begin():
        session.add_all(
            [
                QueryLog(
                    created_at=datetime(2026, 11, 2, tzinfo=UTC),
                    prompt_tokens=300_000,
                    completion_tokens=0,
                    eval_flags={"embedding_tokens": 0},
                ),
                QueryLog(
                    created_at=datetime(2026, 10, 30, tzinfo=UTC),
                    prompt_tokens=9_000_000,
                    completion_tokens=0,
                    eval_flags={},
                ),
            ]
        )
    with Session(migrated_engine) as session:
        assert month_spend(session, NOW, PRICES) == pytest.approx(0.6)
    assert _guard(migrated_engine, cap=1.0).admit("192.0.2.1", NOW).outcome == "ok"
    refused = _guard(migrated_engine, cap=0.5).admit("192.0.2.1", NOW)
    assert refused.outcome == "spend_cap" and refused.retry_after_s > 0
