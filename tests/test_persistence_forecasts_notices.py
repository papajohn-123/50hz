from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.db.base import Base
from app.db.models import ForecastObservation, ReportedNotice
from app.persistence.ingestion import PostgresIngestionRepository
from app.persistence.reads import GridReadRepository
from app.persistence.records import (
    map_carbon_actual_record,
    map_carbon_forecast_record,
    map_demand_forecast_record,
    map_remit_notice_record,
    map_system_warning_record,
    map_wind_forecast_record,
)
from app.sources.types import (
    AdapterResult,
    CarbonIntensityRecord,
    DataClassification,
    DemandForecastRecord,
    GenerationMixShare,
    ObservationWindow,
    OutageProfilePoint,
    RemitUnavailabilityRecord,
    SystemWarningRecord,
    WindForecastRecord,
)


NOW = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
RAW_ID = UUID("a772b564-c87e-4108-b28e-2a7b2a560443")
RUN_ID = UUID("4b2ee67a-9b49-4ef5-a177-aa3e6d36d60c")
WINDOW = ObservationWindow(start=NOW - timedelta(minutes=30), end=NOW)


class FakeScalars:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


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
        self.executed: list[Any] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(self, statement: Any, parameters: Any = None) -> FakeResult:
        self.executed.append(statement)
        assert self.responses, "unexpected statement"
        return self.responses.pop(0)


def carbon(classification: DataClassification) -> CarbonIntensityRecord:
    return CarbonIntensityRecord(
        source_key=f"neso-carbon:national:{NOW.isoformat()}:{classification.value}",
        period_start=NOW,
        period_end=NOW + timedelta(minutes=30),
        retrieved_at=NOW - timedelta(minutes=1),
        intensity_g_co2_per_kwh=67 if classification is DataClassification.OBSERVED else 63,
        classification=classification,
        index="low",
        generation_mix=(GenerationMixShare("wind", 45.5),),
        dataset="carbon_intensity_national",
    )


def remit(revision: int = 4, *, status: str = "Active") -> RemitUnavailabilityRecord:
    return RemitUnavailabilityRecord(
        source_key=f"elexon:REMIT:mrid-1:r{revision}",
        mrid="mrid-1",
        revision_number=revision,
        message_id=903186,
        published_at=NOW - timedelta(minutes=10),
        created_at=NOW - timedelta(hours=1),
        retrieved_at=NOW - timedelta(minutes=1),
        event_start=NOW - timedelta(hours=2),
        event_end=NOW + timedelta(hours=2),
        message_heading="Unit unavailability",
        event_type="Unavailability",
        event_status=status,
        affected_unit="Example Unit 1",
        asset_id="asset-1",
        fuel_type="Nuclear",
        normal_capacity_mw=610,
        available_capacity_mw=106,
        unavailable_capacity_mw=504,
        reported_cause="The participant reports equipment repair work.",
        reported_related_information="Reported by the market participant.",
        outage_profile=(
            OutageProfilePoint(
                start=NOW - timedelta(hours=2),
                end=NOW + timedelta(hours=2),
                available_capacity_mw=106,
            ),
        ),
    )


def warning(text: str, *, retrieved_at: datetime = NOW) -> SystemWarningRecord:
    digest = hashlib.sha256(f"System Warning\0{text}".encode()).hexdigest()
    return SystemWarningRecord(
        source_key=f"elexon:SYSWARN:{NOW.isoformat()}:{digest[:16]}",
        published_at=NOW - timedelta(minutes=5),
        retrieved_at=retrieved_at,
        warning_type="System Warning",
        warning_text=text,
        content_sha256=digest,
    )


def test_carbon_actual_and_forecast_use_separate_storage_contracts() -> None:
    actual = map_carbon_actual_record(
        carbon(DataClassification.OBSERVED),
        source_id="neso.carbon-intensity-national",
        raw_payload_id=RAW_ID,
    )
    forecast = map_carbon_forecast_record(
        carbon(DataClassification.FORECAST),
        source_id="neso.carbon-intensity-national",
        raw_payload_id=RAW_ID,
    )

    assert actual["region_code"] == "GB"
    assert actual["observed_at"] == NOW
    assert actual["generation_mix"] == [{"fuel": "wind", "percent": 45.5}]
    assert forecast["metric_type"] == "carbon_intensity"
    assert forecast["valid_from"] == NOW
    assert forecast["valid_to"] == NOW + timedelta(minutes=30)
    assert forecast["issued_at"] == forecast["retrieved_at"]
    assert forecast["attributes"]["issueTimeBasis"] == "retrieved_at"


def test_demand_forecast_preserves_published_revision_time() -> None:
    published_at = NOW - timedelta(hours=1)
    values = map_demand_forecast_record(
        DemandForecastRecord(
            source_key="elexon:NDF:revision-1",
            forecast_for=NOW + timedelta(hours=1),
            published_at=published_at,
            retrieved_at=NOW,
            demand_mw=29_400,
            boundary="N",
        ),
        source_id="elexon.ndf",
        raw_payload_id=RAW_ID,
    )

    assert values["metric_type"] == "demand"
    assert values["series_key"] == "n"
    assert values["issued_at"] == published_at
    assert values["source_record_id"] == "elexon:NDF:revision-1"


def test_wind_forecast_routes_to_generation_metric() -> None:
    values = map_wind_forecast_record(
        WindForecastRecord(
            source_key="elexon:WINDFOR:revision-1",
            forecast_for=NOW + timedelta(hours=1),
            published_at=NOW - timedelta(minutes=30),
            retrieved_at=NOW,
            generation_mw=5_031,
        ),
        source_id="elexon.windfor",
        raw_payload_id=RAW_ID,
    )

    assert values["metric_type"] == "generation"
    assert values["series_key"] == "wind"
    assert values["value"] == 5_031
    assert values["attributes"]["fuelType"] == "wind"


def test_remit_mapping_keeps_reported_cause_and_profile_without_inference() -> None:
    values = map_remit_notice_record(
        remit(), source_id="elexon.remit", raw_payload_id=RAW_ID
    )

    assert values["external_id"] == "mrid-1"
    assert values["revision_key"] == "r4"
    assert values["classification"] == "reported"
    assert values["reported_cause"] == (
        "The participant reports equipment repair work."
    )
    assert values["evidence"]["messageId"] == 903186
    assert values["evidence"]["outageProfile"][0]["availableCapacityMW"] == 106
    assert len(values["content_sha256"]) == 64


def test_syswarn_text_corrections_share_identity_but_keep_distinct_revisions() -> None:
    first = map_system_warning_record(
        warning("First reported text"),
        source_id="elexon.syswarn",
        raw_payload_id=RAW_ID,
    )
    corrected = map_system_warning_record(
        warning("Corrected reported text"),
        source_id="elexon.syswarn",
        raw_payload_id=RAW_ID,
    )

    assert first["external_id"] == corrected["external_id"]
    assert first["revision_key"] != corrected["revision_key"]
    assert first["warning_text"] == "First reported text"
    assert corrected["warning_text"] == "Corrected reported text"


def test_mixed_carbon_result_persists_actual_and_forecast_atomically() -> None:
    session = FakeSession(
        [
            FakeResult(),
            FakeResult([RUN_ID]),
            FakeResult([RAW_ID]),
            FakeResult([True]),
            FakeResult([True]),
            FakeResult(),
        ]
    )
    repository = PostgresIngestionRepository(lambda: session)
    result = AdapterResult(
        source_id="neso.carbon.national.current",
        dataset="carbon_intensity_national",
        endpoint="intensity",
        window=WINDOW,
        retrieved_at=NOW,
        request_url="https://api.carbonintensity.org.uk/intensity",
        records=(
            carbon(DataClassification.OBSERVED),
            carbon(DataClassification.FORECAST),
        ),
        raw_payload={"data": []},
        raw_body=b'{"data":[]}',
        checksum_sha256="c" * 64,
    )

    outcome = asyncio.run(
        repository.persist_success(
            job_id="neso.carbon.current",
            result=result,
            attempted_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
        )
    )

    assert outcome.inserted == 2
    assert {getattr(statement.table, "name", None) for statement in session.executed} >= {
        "carbon_observations",
        "forecast_observations",
    }


def test_remit_revisions_persist_as_two_reported_notice_rows() -> None:
    session = FakeSession(
        [
            FakeResult(),
            FakeResult([RUN_ID]),
            FakeResult([RAW_ID]),
            FakeResult([True, True]),
            FakeResult(),
        ]
    )
    repository = PostgresIngestionRepository(lambda: session)
    result = AdapterResult(
        source_id="elexon.remit.unavailability",
        dataset="REMIT",
        endpoint="remit/list/by-publish/stream",
        window=WINDOW,
        retrieved_at=NOW,
        request_url="https://data.elexon.co.uk/bmrs/api/v1/remit/list/by-publish/stream",
        records=(remit(3), remit(4)),
        raw_payload={"listing": [], "details": []},
        raw_body=b"{}",
        checksum_sha256="d" * 64,
    )

    outcome = asyncio.run(
        repository.persist_success(
            job_id="elexon.remit",
            result=result,
            attempted_at=NOW,
            completed_at=NOW + timedelta(seconds=1),
        )
    )

    assert outcome.inserted == 2
    notice_statement = next(
        statement
        for statement in session.executed
        if getattr(getattr(statement, "table", None), "name", None)
        == "reported_notices"
    )
    assert notice_statement.table.name == "reported_notices"


def test_forecast_and_active_notice_reads_are_source_neutral() -> None:
    forecast_row = ForecastObservation(
        source_id="elexon.ndf",
        raw_payload_id=RAW_ID,
        source_record_id="ndf-r1",
        metric_type="demand",
        series_key="n",
        variant="point",
        value=29_400,
        unit="MW",
        valid_from=NOW + timedelta(hours=1),
        valid_to=None,
        issued_at=NOW - timedelta(hours=1),
        published_at=NOW - timedelta(hours=1),
        retrieved_at=NOW,
        model_name="NDF",
        attributes={"classification": "forecast"},
    )
    forecast_session = FakeSession([FakeResult([forecast_row])])
    forecasts = asyncio.run(
        GridReadRepository(lambda: forecast_session).get_forecasts(
            window_start=NOW,
            window_end=NOW + timedelta(hours=2),
        )
    )
    assert forecasts[0].metric_type == "demand"
    assert forecasts[0].issued_at == NOW - timedelta(hours=1)

    active_remit = reported_notice(remit())
    cancelled = reported_notice(remit(revision=5, status="Cancelled"), external_id="mrid-2")
    fresh_warning = reported_notice_from_warning(warning("A current warning"))
    stale_warning = reported_notice_from_warning(
        warning("An old warning", retrieved_at=NOW - timedelta(hours=1))
    )
    notice_session = FakeSession(
        [FakeResult([active_remit, cancelled, fresh_warning, stale_warning])]
    )
    notices = asyncio.run(
        GridReadRepository(lambda: notice_session, clock=lambda: NOW).get_active_notices()
    )
    assert {(notice.notice_kind, notice.external_id) for notice in notices} == {
        ("remit_unavailability", "mrid-1"),
        ("system_warning", fresh_warning.external_id),
    }
    assert notices[0].reported_cause is not None


def reported_notice(
    record: RemitUnavailabilityRecord, *, external_id: str | None = None
) -> ReportedNotice:
    values = map_remit_notice_record(
        record, source_id="elexon.remit", raw_payload_id=RAW_ID
    )
    if external_id is not None:
        values["external_id"] = external_id
    return ReportedNotice(
        id=UUID(int=record.revision_number),
        **values,
    )


def reported_notice_from_warning(record: SystemWarningRecord) -> ReportedNotice:
    return ReportedNotice(
        id=UUID(record.content_sha256[:32]),
        **map_system_warning_record(
            record, source_id="elexon.syswarn", raw_payload_id=RAW_ID
        ),
    )


def test_schema_has_revision_safe_reported_notice_identity() -> None:
    table = Base.metadata.tables["reported_notices"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert (
        "source_id",
        "notice_kind",
        "external_id",
        "revision_key",
    ) in unique_columns
    assert "reported_cause" in table.c
    assert "evidence" in table.c
