from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.game.models import PredictionResolution
from app.game.resolution import build_prediction_resolution
from app.persistence.game import PostgresPredictionResolutionLedger
from app.persistence.reads import InterconnectorRead, ReadProvenance


DAY = date(2026, 7, 11)
TARGET = datetime(2026, 7, 11, 17, tzinfo=UTC)


def flow(value: float) -> InterconnectorRead:
    return InterconnectorRead(
        connector_id="IFA",
        display_name="IFA",
        counterparty="France",
        megawatts=value,
        provenance=ReadProvenance(
            source_id="elexon.fuelinst",
            source_record_id=f"IFA:{value}",
            observed_at=TARGET,
            published_at=TARGET + timedelta(minutes=1),
            retrieved_at=TARGET + timedelta(minutes=2),
        ),
    )


def terminal(value: float = 500) -> PredictionResolution:
    return build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(flow(value),),
    )


class FakeResult:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResult]) -> None:
        self.responses = list(responses)
        self.executed: list[tuple[Any, Any]] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> Transaction:
        return Transaction()

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, statement: Any, parameters: Any = None) -> FakeResult:
        self.executed.append((statement, parameters))
        assert self.responses, "unexpected prediction-resolution statement"
        return self.responses.pop(0)


def test_first_terminal_result_is_revision_one_with_postgres_conflict_guards() -> None:
    session = FakeSession(
        [
            FakeResult(),  # advisory transaction lock
            FakeResult(),  # evidence checksum lookup
            FakeResult(),  # latest revision lookup
            FakeResult(uuid.uuid4()),  # insert returning id
        ]
    )
    ledger = PostgresPredictionResolutionLedger(lambda: session)

    result = asyncio.run(ledger.persist(terminal()))

    assert result.resolution_revision == 1
    assert result.is_correction is False
    assert "pg_advisory_xact_lock" in str(session.executed[0][0])
    assert -(2**63) <= session.executed[0][1]["lock_key"] < 2**63
    insertion = session.executed[-1][0]
    sql = str(insertion.compile(dialect=postgresql.dialect())).upper()
    assert "ON CONFLICT DO NOTHING" in sql
    assert "RETURNING PREDICTION_RESOLUTION_REVISIONS.ID" in sql
    params = insertion.compile(dialect=postgresql.dialect()).params
    assert params["resolution_revision"] == 1
    assert params["payload"]["resolutionRevision"] == 1
    assert params["payload"]["isCorrection"] is False


def test_changed_evidence_appends_an_explicit_correction_revision() -> None:
    session = FakeSession(
        [
            FakeResult(),
            FakeResult(),
            FakeResult(SimpleNamespace(resolution_revision=1)),
            FakeResult(uuid.uuid4()),
        ]
    )
    ledger = PostgresPredictionResolutionLedger(lambda: session)

    result = asyncio.run(ledger.persist(terminal(-500)))

    assert result.resolution_revision == 2
    assert result.is_correction is True
    insertion = session.executed[-1][0]
    params = insertion.compile(dialect=postgresql.dialect()).params
    assert params["resolution_revision"] == 2
    assert params["payload"]["isCorrection"] is True


def test_identical_evidence_is_idempotent_and_returns_the_immutable_row() -> None:
    original = terminal().model_copy(
        update={"resolution_revision": 1, "is_correction": False}
    )
    stored = SimpleNamespace(payload=original.model_dump(mode="json", by_alias=True))
    session = FakeSession([FakeResult(), FakeResult(stored)])
    ledger = PostgresPredictionResolutionLedger(lambda: session)

    repeated = asyncio.run(ledger.persist(terminal()))

    assert repeated == original
    assert len(session.executed) == 2
    assert all("INSERT INTO" not in str(statement) for statement, _ in session.executed)


def test_pending_results_are_never_written_to_the_immutable_ledger() -> None:
    pending = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=4),
    )
    called = False

    def factory() -> FakeSession:
        nonlocal called
        called = True
        return FakeSession([])

    ledger = PostgresPredictionResolutionLedger(factory)

    with pytest.raises(ValueError, match="pending"):
        asyncio.run(ledger.persist(pending))
    assert called is False
