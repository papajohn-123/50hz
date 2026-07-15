from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.geography.records import REPD_ASSET_TYPE, REPD_SOURCE_ID
from app.geography.repd import (
    REPD_DATASET_NAME,
    REPD_LICENCE_NAME,
    REPD_LICENCE_URL,
    REPD_PUBLISHER,
    REPDProvenance,
    REPDSite,
    REPDStatus,
)
from app.persistence.ingestion import (
    PostgresIngestionRepository,
    _map_record_batches,
    _persist_repd_snapshot,
    _validate_repd_snapshot_contract,
)
from app.sources.types import AdapterResult, ObservationWindow


NOW = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)
WINDOW = ObservationWindow(start=NOW - timedelta(days=1), end=NOW)
SOURCE_URL = (
    "https://assets.publishing.service.gov.uk/media/hash/"
    "REPD_publication_Q1_2026.csv"
)
RUN_ID = UUID("b90fd922-73f7-5cb5-b404-d258a8c88283")
RAW_ID = UUID("611cc02f-de6d-58f0-9537-ec43f724d99a")


def site(source_id: str = "1001") -> REPDSite:
    return REPDSite(
        source_id=source_id,
        project_name=f"Fen Solar {source_id}",
        operator_name="Fen Power Ltd",
        technology="Solar Photovoltaics",
        capacity_mw=42.5,
        status=REPDStatus.OPERATIONAL,
        source_status="Operational",
        storage_type=None,
        is_storage=False,
        region="East of England",
        country="England",
        planning_authority="Fen Council",
        record_last_updated="01/04/2026",
        coordinates=None,
        provenance=REPDProvenance(
            publisher=REPD_PUBLISHER,
            dataset=REPD_DATASET_NAME,
            source_url=SOURCE_URL,
            licence_name=REPD_LICENCE_NAME,
            licence_url=REPD_LICENCE_URL,
            retrieved_at=NOW,
        ),
    )


def result(*records: REPDSite) -> AdapterResult[REPDSite]:
    count = len(records)
    return AdapterResult(
        source_id=REPD_SOURCE_ID,
        dataset="REPD",
        endpoint=(
            "api/content/government/publications/"
            "renewable-energy-planning-database-quarterly-extract"
        ),
        window=WINDOW,
        retrieved_at=NOW,
        request_url=SOURCE_URL,
        records=records,
        raw_payload={
            "publication": {"contentApiURL": "https://www.gov.uk/api/content/x"},
            "attachment": {"url": SOURCE_URL},
            "parse": {"inputRows": count, "retainedRows": count},
        },
        raw_body=b"source csv bytes",
        checksum_sha256="a" * 64,
        content_type="text/csv",
        metadata={
            "snapshotKind": "complete_reference",
            "recordCount": count,
            "attachmentURL": SOURCE_URL,
        },
    )


class _Scalars:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class _Result:
    def __init__(self, values: list[Any] | None = None) -> None:
        self.values = values or []

    def scalars(self) -> _Scalars:
        return _Scalars(self.values)

    def scalar_one(self) -> Any:
        assert len(self.values) == 1
        return self.values[0]

    def scalar_one_or_none(self) -> Any | None:
        assert len(self.values) <= 1
        return self.values[0] if self.values else None


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.statements: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        assert self.results, "unexpected persistence statement"
        return self.results.pop(0)


def test_complete_snapshot_contract_is_explicit_and_skips_generic_batches() -> None:
    snapshot = result(site())

    _validate_repd_snapshot_contract(snapshot)
    batches = _map_record_batches(
        snapshot.records,
        source_id=REPD_SOURCE_ID,
        raw_payload_id=RAW_ID,
    )

    assert batches == ()


@pytest.mark.parametrize(
    "snapshot",
    [
        result(),
        replace(result(site()), metadata={"snapshotKind": "partial", "recordCount": 1}),
        replace(
            result(site()),
            metadata={"snapshotKind": "complete_reference", "recordCount": 2},
        ),
        replace(
            result(site()),
            raw_payload={"parse": {"inputRows": 2, "retainedRows": 2}},
        ),
    ],
)
def test_empty_or_partial_snapshot_is_rejected_before_membership_changes(
    snapshot: AdapterResult[REPDSite],
) -> None:
    with pytest.raises(ValueError):
        _validate_repd_snapshot_contract(snapshot)


def test_snapshot_upsert_uses_deterministic_ids_and_tightly_scoped_deactivation() -> None:
    first_session = _Session([_Result([True, False]), _Result([UUID(int=5)])])
    second_session = _Session([_Result([True, False]), _Result([])])
    records = (site("1001"), site("1002"))

    first = asyncio.run(
        _persist_repd_snapshot(
            first_session,
            source_id=REPD_SOURCE_ID,
            records=records,
        )
    )
    asyncio.run(
        _persist_repd_snapshot(
            second_session,
            source_id=REPD_SOURCE_ID,
            records=records,
        )
    )

    assert first == (1, 2, 0)
    first_insert = first_session.statements[0].compile(dialect=postgresql.dialect())
    second_insert = second_session.statements[0].compile(dialect=postgresql.dialect())
    first_ids = sorted(
        str(value)
        for value in first_insert.params.values()
        if isinstance(value, UUID)
    )
    second_ids = sorted(
        str(value)
        for value in second_insert.params.values()
        if isinstance(value, UUID)
    )
    assert first_ids == second_ids
    assert len(first_ids) == 2

    deactivation = first_session.statements[1].compile(dialect=postgresql.dialect())
    sql = str(deactivation).lower()
    assert "update assets" in sql
    assert "assets.source_id" in sql
    assert "assets.asset_type" in sql
    assert "assets.active is true" in sql
    assert "not in" in sql
    assert REPD_SOURCE_ID in deactivation.params.values()
    assert REPD_ASSET_TYPE in deactivation.params.values()
    assert ["1001", "1002"] in deactivation.params.values()


def test_empty_helper_refuses_to_issue_any_sql() -> None:
    session = _Session([])

    with pytest.raises(ValueError, match="empty REPD"):
        asyncio.run(
            _persist_repd_snapshot(
                session,
                source_id=REPD_SOURCE_ID,
                records=(),
            )
        )

    assert session.statements == []


def test_repository_persists_repd_metadata_and_assets_in_one_transaction() -> None:
    session = _Session(
        [
            _Result(),
            _Result([RUN_ID]),
            _Result([RAW_ID]),
            _Result([True]),
            _Result([]),
            _Result(),
        ]
    )
    repository = PostgresIngestionRepository(lambda: session)

    outcome = asyncio.run(
        repository.persist_success(
            job_id="desnz.repd",
            result=result(site()),
            attempted_at=NOW,
            completed_at=NOW + timedelta(seconds=2),
        )
    )

    assert outcome.inserted == 1
    assert outcome.updated == 0
    assert outcome.unchanged == 0
    assert session.results == []
    source_upsert = session.statements[0].compile(dialect=postgresql.dialect())
    assert REPD_SOURCE_ID in source_upsert.params.values()
    assert "https://www.gov.uk" in source_upsert.params.values()
    assert "Open Government Licence v3.0" in source_upsert.params.values()
    assert 91 * 24 * 60 * 60 in source_upsert.params.values()
