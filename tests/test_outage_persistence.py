from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import DistributionIncidentRevision
from app.outages.repository import _current_incidents_statement
from app.persistence.ingestion import (
    PostgresIngestionRepository,
    _DISTRIBUTION_INCIDENT_SPEC,
    _identity_key,
    _prepare_immutable_revisions,
    _refresh_distribution_incident_current,
)
from app.persistence.records import map_distribution_incident_record
from app.sources.types import (
    AdapterResult,
    DistributionIncidentRecord,
    ObservationWindow,
)


NOW = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
RAW_ID = UUID("76517619-59c5-412f-8229-54c224c70201")


def record(
    *,
    customers: int = 42,
    retrieved_at: datetime = NOW,
    official_summary: str = "Engineers are investigating.",
    checksum: str = "a" * 64,
) -> DistributionIncidentRecord:
    return DistributionIncidentRecord(
        source_key="ukpn:LIVE_FAULTS:INCD-1",
        incident_reference="INCD-1",
        status="unplanned",
        source_created_at=NOW - timedelta(hours=1),
        observed_at=NOW - timedelta(minutes=20),
        retrieved_at=retrieved_at,
        incident_start=NOW - timedelta(hours=1),
        estimated_restoration_at=NOW + timedelta(hours=1),
        status_id=1,
        customers_affected=customers,
        calls_reported=3,
        postcode_sectors=("SW1A 1",),
        outward_codes=("SW1A",),
        latitude=51.501,
        longitude=-0.141,
        geography_precision="aggregated_incident_point",
        operating_zone="LONDON",
        official_summary=official_summary,
        official_details="A cable fault has been reported.",
        restoration_window_text="Today 12:00 - 13:00",
        incident_category="24",
        content_sha256=checksum,
    )


def test_mapping_retains_only_coarse_geography_and_reported_evidence() -> None:
    values = map_distribution_incident_record(
        record(),
        source_id="ukpn.live-faults",
        raw_payload_id=RAW_ID,
    )

    assert values["postcode_sectors"] == ["SW1A 1"]
    assert values["outward_codes"] == ["SW1A"]
    assert values["geography_precision"] == "aggregated_incident_point"
    assert values["classification"] == "reported"
    assert values["customers_affected"] == 42
    assert "full_postcode" not in values


def test_mapping_rejects_a_full_postcode_hidden_in_normalized_text() -> None:
    with pytest.raises(ValueError, match="full postcode"):
        map_distribution_incident_record(
            record(official_summary="Fault reported at SW1A 1AA"),
            source_id="ukpn.live-faults",
            raw_payload_id=RAW_ID,
        )


def test_retrieval_only_change_is_idempotent_but_factual_change_adds_revision() -> None:
    original_values = map_distribution_incident_record(
        record(), source_id="ukpn.live-faults", raw_payload_id=RAW_ID
    )
    existing = DistributionIncidentRevision(**original_values)
    existing.revision = 0
    latest = {_identity_key(_DISTRIBUTION_INCIDENT_SPEC, existing): existing}

    retrieval_only = map_distribution_incident_record(
        record(retrieved_at=NOW + timedelta(minutes=5)),
        source_id="ukpn.live-faults",
        raw_payload_id=RAW_ID,
    )
    prepared, inserted, corrected, unchanged = _prepare_immutable_revisions(
        _DISTRIBUTION_INCIDENT_SPEC,
        [retrieval_only],
        latest,
    )
    assert prepared == []
    assert (inserted, corrected, unchanged) == (0, 0, 1)

    correction = map_distribution_incident_record(
        record(customers=51, checksum="b" * 64),
        source_id="ukpn.live-faults",
        raw_payload_id=RAW_ID,
    )
    prepared, inserted, corrected, unchanged = _prepare_immutable_revisions(
        _DISTRIBUTION_INCIDENT_SPEC,
        [correction],
        latest,
    )
    assert prepared[0]["revision"] == 1
    assert prepared[0]["customers_affected"] == 51
    assert (inserted, corrected, unchanged) == (0, 1, 0)


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return None


def test_current_snapshot_membership_is_replaced_atomically_and_idempotently() -> None:
    session = RecordingSession()
    asyncio.run(
        _refresh_distribution_incident_current(
            session,
            source_id="ukpn.live-faults",
            records=(record(),),
            seen_at=NOW,
        )
    )

    assert len(session.statements) == 2
    update_sql = str(
        session.statements[0].compile(dialect=postgresql.dialect())
    ).upper()
    upsert_sql = str(
        session.statements[1].compile(dialect=postgresql.dialect())
    ).upper()
    assert "UPDATE DISTRIBUTION_INCIDENT_CURRENT" in update_sql
    assert "PRESENT" in update_sql
    assert "ON CONFLICT" in upsert_sql
    assert "DO UPDATE" in upsert_sql
    assert "LAST_SEEN_AT" in upsert_sql


def test_successful_empty_snapshot_marks_previous_membership_absent() -> None:
    session = RecordingSession()
    asyncio.run(
        _refresh_distribution_incident_current(
            session,
            source_id="ukpn.live-faults",
            records=(),
            seen_at=NOW,
        )
    )
    assert len(session.statements) == 1
    assert "UPDATE distribution_incident_current" in str(session.statements[0])


def test_persistence_rejects_unsanitized_raw_payload_before_opening_a_session() -> None:
    def forbidden_session():
        raise AssertionError("database must not be touched")

    result = AdapterResult(
        source_id="ukpn.live_faults",
        dataset="LIVE_FAULTS",
        endpoint="live-faults",
        window=ObservationWindow(NOW - timedelta(minutes=5), NOW),
        retrieved_at=NOW,
        request_url="https://ukpn.example.test/live-faults",
        records=(),
        raw_payload={
            "total_count": 1,
            "results": [{"fullpostcodedata": "SW1A1AA"}],
        },
        raw_body=b"{}",
        checksum_sha256="c" * 64,
    )

    with pytest.raises(ValueError, match="privacy-reduced"):
        asyncio.run(
            PostgresIngestionRepository(forbidden_session).persist_success(
                job_id="ukpn.live_faults",
                result=result,
                attempted_at=NOW,
                completed_at=NOW + timedelta(seconds=1),
            )
        )


def test_current_query_ranks_revisions_before_filtering_latest_membership() -> None:
    sql = str(
        _current_incidents_statement(include_restored=False, limit=50).compile(
            dialect=postgresql.dialect()
        )
    ).lower()
    assert "row_number() over" in sql
    assert "distribution_incident_current.present is true" in sql
    assert "distribution_incident_revisions.status in" in sql
    assert "limit" in sql
