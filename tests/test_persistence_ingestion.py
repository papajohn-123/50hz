from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.dialects import postgresql

from app.db.models import GenerationObservation, IngestionRun
from app.domain.enums import IngestionRunStatus
from app.persistence.ingestion import PostgresIngestionRepository
from app.persistence.records import map_generation_record
from app.sources.types import AdapterResult, GenerationRecord, ObservationWindow


NOW = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
WINDOW = ObservationWindow(start=NOW - timedelta(minutes=30), end=NOW)
RUN_ID = UUID("227a850f-186a-4e16-aef7-2f12f984ad53")
RAW_ID = UUID("8a5fa578-2579-4aa9-b24c-711683cc6b1f")


class FakeScalars:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values

    def first(self) -> Any | None:
        return self.values[0] if self.values else None


class FakeResult:
    def __init__(self, values: list[Any] | None = None) -> None:
        self.values = values or []

    def scalars(self) -> FakeScalars:
        return FakeScalars(self.values)

    def scalar_one(self) -> Any:
        assert len(self.values) == 1
        return self.values[0]

    def scalar_one_or_none(self) -> Any | None:
        assert len(self.values) <= 1
        return self.values[0] if self.values else None


class FakeTransaction:
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

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(self, statement: Any, parameters: Any = None) -> FakeResult:
        self.executed.append((statement, parameters))
        assert self.responses, "unexpected persistence statement"
        return self.responses.pop(0)


def generation(fuel: str, megawatts: float) -> GenerationRecord:
    return GenerationRecord(
        source_key=f"elexon:FUELINST:{NOW.isoformat()}:{fuel}",
        observed_at=NOW - timedelta(minutes=15),
        published_at=NOW - timedelta(minutes=14),
        retrieved_at=NOW - timedelta(minutes=13),
        fuel_code=fuel,
        fuel_type=fuel.lower(),
        generation_mw=megawatts,
    )


def adapter_result() -> AdapterResult[GenerationRecord]:
    return AdapterResult(
        source_id="elexon.fuelinst",
        dataset="FUELINST",
        endpoint="datasets/FUELINST",
        window=WINDOW,
        retrieved_at=NOW,
        request_url="https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST",
        records=(
            generation("WIND", 13_500),
            generation("NUCLEAR", 4_250),
            generation("CCGT", 8_500),
        ),
        raw_payload={"data": []},
        raw_body=b'{"data":[]}',
        checksum_sha256="a" * 64,
        content_type="application/json",
    )


def stored_generation(
    record: GenerationRecord,
    *,
    revision: int = 0,
) -> GenerationObservation:
    values = map_generation_record(
        record,
        source_id="elexon.fuelinst",
        raw_payload_id=RAW_ID,
    )
    values["revision"] = revision
    return GenerationObservation(**values)


def test_persist_success_deduplicates_raw_data_and_classifies_upserts() -> None:
    existing_wind = stored_generation(generation("WIND", 12_000))
    existing_nuclear = stored_generation(generation("NUCLEAR", 4_250))
    session = FakeSession(
        [
            FakeResult(),  # source metadata upsert
            FakeResult([RUN_ID]),  # ingestion run upsert
            FakeResult(),  # raw payload conflict: no inserted id
            FakeResult([RAW_ID]),  # fetch checksum-deduplicated raw id
            FakeResult([existing_wind, existing_nuclear]),  # latest revisions in bulk
            FakeResult(
                [
                    UUID("90c62fa3-79fc-43a2-b85d-b8f037523dc6"),
                    UUID("f9d83ed8-9327-4744-a705-4eb5f32230da"),
                ]
            ),  # one correction plus one new identity
            FakeResult(),  # complete run
        ]
    )
    repository = PostgresIngestionRepository(lambda: session)

    outcome = asyncio.run(
        repository.persist_success(
            job_id="elexon.fuelinst",
            result=adapter_result(),
            attempted_at=NOW,
            completed_at=NOW + timedelta(seconds=2),
        )
    )

    assert outcome.inserted == 1
    assert outcome.updated == 1
    assert outcome.unchanged == 1
    assert session.responses == []

    statements = [statement for statement, _ in session.executed]
    table_statements = {
        statement.table.name: statement
        for statement in statements
        if getattr(statement, "table", None) is not None
    }
    generation_upsert = table_statements["generation_observations"]
    generation_sql = str(
        generation_upsert.compile(dialect=postgresql.dialect())
    ).upper()
    assert "ON CONFLICT" in generation_sql
    assert "DO NOTHING" in generation_sql
    assert "DO UPDATE" not in generation_sql
    assert "IS DISTINCT FROM" not in generation_sql
    assert "XMAX = 0" not in generation_sql
    revision_parameters = [
        value
        for key, value in generation_upsert.compile(
            dialect=postgresql.dialect()
        ).params.items()
        if "revision" in key
    ]
    assert sorted(revision_parameters) == [0, 1]

    raw_insert = next(
        statement
        for statement in statements
        if getattr(getattr(statement, "table", None), "name", None) == "raw_payloads"
    )
    assert "DO NOTHING" in str(
        raw_insert.compile(dialect=postgresql.dialect())
    ).upper()


def test_checkpoint_uses_latest_attempt_but_preserves_last_success_window() -> None:
    latest_failure = IngestionRun(
        id=UUID("209b3383-74bc-4589-9f64-fc76fbb51983"),
        source_id="elexon.fuelinst",
        adapter="elexon.fuelinst",
        endpoint="datasets/FUELINST",
        idempotency_key="failed",
        requested_from=NOW - timedelta(minutes=10),
        requested_to=NOW,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
        status=IngestionRunStatus.FAILED,
        records_received=0,
        records_written=0,
        cursor={},
        error={"type": "TimeoutError"},
    )
    last_success = IngestionRun(
        id=UUID("6fa40b48-0a27-4591-9655-9ba45aed0f28"),
        source_id="elexon.fuelinst",
        adapter="elexon.fuelinst",
        endpoint="datasets/FUELINST",
        idempotency_key="success",
        requested_from=NOW - timedelta(minutes=20),
        requested_to=NOW - timedelta(minutes=2),
        started_at=NOW - timedelta(minutes=2),
        completed_at=NOW - timedelta(minutes=2) + timedelta(seconds=2),
        status=IngestionRunStatus.SUCCEEDED,
        records_received=10,
        records_written=10,
        cursor={},
        error=None,
    )
    session = FakeSession([FakeResult([latest_failure]), FakeResult([last_success])])
    repository = PostgresIngestionRepository(lambda: session)

    checkpoint = asyncio.run(repository.get_checkpoint("elexon.fuelinst"))

    assert checkpoint is not None
    assert checkpoint.last_attempted_at == NOW
    assert checkpoint.last_succeeded_at == last_success.completed_at
    assert checkpoint.window_end == last_success.requested_to


def test_record_failure_is_idempotent_and_bounds_error_payload() -> None:
    session = FakeSession([FakeResult(), FakeResult()])
    repository = PostgresIngestionRepository(lambda: session)

    asyncio.run(
        repository.record_failure(
            job_id="elexon.fuelinst",
            window=WINDOW,
            attempted_at=NOW,
            failed_at=NOW + timedelta(seconds=1),
            error_type="SourceUnavailableError",
            error_message="x" * 3000,
        )
    )

    failure_insert = session.executed[1][0]
    compiled = failure_insert.compile(dialect=postgresql.dialect())
    assert "ON CONFLICT" in str(compiled).upper()
    error_parameter = next(
        value
        for key, value in compiled.params.items()
        if key.startswith("error") and isinstance(value, dict)
    )
    assert len(error_parameter["message"]) == 2000
