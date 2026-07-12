from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.briefing import present_today_briefing
from app.api.dependencies import get_grid_read_repository
from app.briefing import (
    BriefingStatus,
    CurrentPositionStatus,
    ReportedEventTiming,
    SourceState,
)
from app.main import app
from app.persistence import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    ForecastRead,
    FrequencyRead,
    ReadProvenance,
    ReportedNoticeRead,
    SourceMetadataRead,
)


NOW = datetime(2026, 7, 11, 19, 10, tzinfo=UTC)


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _next_half_hour(value: datetime) -> datetime:
    floor = value.replace(
        minute=30 if value.minute >= 30 else 0,
        second=0,
        microsecond=0,
    )
    return floor + timedelta(minutes=30) if floor <= value else floor


def _provenance(
    source_id: str,
    observed_at: datetime,
    *,
    value_id: str,
) -> ReadProvenance:
    return ReadProvenance(
        source_id=source_id,
        source_record_id=value_id,
        observed_at=observed_at,
        published_at=observed_at + timedelta(minutes=1),
        retrieved_at=observed_at + timedelta(minutes=5),
    )


SOURCES = (
    SourceMetadataRead(
        "elexon.indo",
        "elexon",
        "INDO",
        "Elexon — INDO",
        None,
        None,
        None,
        1_800,
    ),
    SourceMetadataRead(
        "elexon.freq",
        "elexon",
        "FREQ",
        "Elexon — FREQ",
        None,
        None,
        None,
        60,
    ),
    SourceMetadataRead(
        "neso.carbon-intensity-national",
        "neso",
        "CARBON_INTENSITY_NATIONAL",
        "NESO — national carbon intensity",
        None,
        None,
        None,
        1_800,
    ),
)


class FullBriefingRepository:
    async def get_current(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CurrentGridRead:
        assert as_of is not None
        observed = _floor_hour(as_of)
        latest = observed.hour % 2 == 1
        return CurrentGridRead(
            requested_at=as_of,
            generation=(),
            demand=DemandRead(
                "gb",
                "indo",
                30_000 if latest else 29_000,
                _provenance("elexon.indo", observed, value_id=f"indo:{observed}"),
            ),
            frequency=FrequencyRead(
                "gb",
                50.03 if latest else 50.0,
                _provenance("elexon.freq", observed, value_id=f"freq:{observed}"),
            ),
            interconnectors=(),
            carbon=CarbonRead(
                carbon_region,
                80 if latest else 120,
                "low" if latest else "moderate",
                (),
                _provenance(
                    "neso.carbon-intensity-national",
                    observed,
                    value_id=f"carbon:{observed}",
                ),
            ),
            sources=SOURCES,
        )

    async def get_forecasts(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        metric_types: tuple[str, ...] | None = None,
        series_key: str | None = None,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]:
        issued = (issued_before or window_start) - timedelta(minutes=20)
        captured = (issued_before or window_start) - timedelta(minutes=10)
        start = _next_half_hour(window_start)
        rows: list[ForecastRead] = []
        for offset, carbon, demand, wind in (
            (0, 110, 31_000, 5_000),
            (1, 65, 34_000, 7_500),
            (2, 90, 32_000, 6_000),
        ):
            valid = start + timedelta(hours=offset)
            rows.extend(
                (
                    _forecast(
                        source_id="neso.carbon-intensity-national",
                        metric_type="carbon_intensity",
                        series_key="GB",
                        value=carbon,
                        unit="gCO2/kWh",
                        valid_from=valid,
                        valid_to=valid + timedelta(minutes=30),
                        issued_at=issued,
                        retrieved_at=captured,
                        model="neso_carbon_intensity",
                        dataset="CARBON_INTENSITY_NATIONAL",
                    ),
                    _forecast(
                        source_id="elexon.ndf",
                        metric_type="demand",
                        series_key="n",
                        value=demand,
                        unit="MW",
                        valid_from=valid,
                        valid_to=None,
                        issued_at=issued,
                        retrieved_at=captured,
                        model="NDF",
                        dataset="NDF",
                    ),
                    _forecast(
                        source_id="elexon.windfor",
                        metric_type="generation",
                        series_key="wind",
                        value=wind,
                        unit="MW",
                        valid_from=valid,
                        valid_to=None,
                        issued_at=issued,
                        retrieved_at=captured,
                        model="WINDFOR",
                        dataset="WINDFOR",
                    ),
                )
            )
        return tuple(rows)

    async def get_carbon_forecast_history(
        self,
        *,
        region_code: str,
        window_start: datetime,
        window_end: datetime,
        captured_after: datetime,
        captured_before: datetime,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]:
        captured = captured_before - timedelta(minutes=10)
        start = window_start.replace(
            minute=30 if window_start.minute >= 30 else 0,
            second=0,
            microsecond=0,
        )
        rows = []
        cursor = start
        index = 0
        while cursor < min(window_end, start + timedelta(hours=24)):
            rows.append(
                _forecast(
                    source_id="neso.carbon-intensity-national",
                    metric_type="carbon_intensity",
                    series_key=region_code,
                    value=45 if 8 <= index <= 9 else 125 + index,
                    unit="gCO2/kWh",
                    valid_from=cursor,
                    valid_to=cursor + timedelta(minutes=30),
                    issued_at=captured,
                    retrieved_at=captured,
                    model="neso_carbon_intensity",
                    dataset="CARBON_INTENSITY_NATIONAL",
                    issue_time_basis="retrieved_at",
                )
            )
            cursor += timedelta(minutes=30)
            index += 1
        return tuple(rows)

    async def get_briefing_notices(
        self,
        *,
        as_of: datetime,
        upcoming_until: datetime,
        warning_fresh_for_seconds: int = 900,
    ) -> tuple[ReportedNoticeRead, ...]:
        return (
            _notice(
                kind="system_warning",
                published_at=as_of - timedelta(minutes=5),
                retrieved_at=as_of - timedelta(minutes=2),
                starts_at=None,
                ends_at=None,
            ),
            _notice(
                kind="remit_unavailability",
                published_at=as_of - timedelta(minutes=30),
                retrieved_at=as_of - timedelta(minutes=2),
                starts_at=as_of + timedelta(hours=2),
                ends_at=as_of + timedelta(hours=5),
            ),
        )


class PartialBriefingRepository(FullBriefingRepository):
    async def get_forecasts(self, **_: object) -> tuple[ForecastRead, ...]:
        raise RuntimeError("forecast read unavailable")

    async def get_carbon_forecast_history(self, **_: object) -> tuple[ForecastRead, ...]:
        raise RuntimeError("forecast history unavailable")


class EmptyBriefingRepository(FullBriefingRepository):
    async def get_current(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CurrentGridRead:
        assert as_of is not None
        return CurrentGridRead(
            requested_at=as_of,
            generation=(),
            demand=None,
            frequency=None,
            interconnectors=(),
            carbon=None,
            sources=(),
        )

    async def get_forecasts(self, **_: object) -> tuple[ForecastRead, ...]:
        return ()

    async def get_carbon_forecast_history(self, **_: object) -> tuple[ForecastRead, ...]:
        return ()

    async def get_briefing_notices(self, **_: object) -> tuple[ReportedNoticeRead, ...]:
        return ()


class IncompatiblePreviousRepository(FullBriefingRepository):
    async def get_current(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CurrentGridRead:
        read = await super().get_current(as_of=as_of, carbon_region=carbon_region)
        assert as_of is not None
        if as_of < NOW - timedelta(minutes=30):
            demand = read.demand
            assert demand is not None
            read = CurrentGridRead(
                requested_at=read.requested_at,
                generation=read.generation,
                demand=DemandRead(
                    demand.series_key,
                    demand.demand_type,
                    demand.megawatts,
                    ReadProvenance(
                        "different.indo",
                        demand.provenance.source_record_id,
                        demand.provenance.observed_at,
                        demand.provenance.published_at,
                        demand.provenance.retrieved_at,
                    ),
                ),
                frequency=read.frequency,
                interconnectors=read.interconnectors,
                carbon=read.carbon,
                sources=read.sources,
            )
        return read


def _forecast(
    *,
    source_id: str,
    metric_type: str,
    series_key: str,
    value: float,
    unit: str,
    valid_from: datetime,
    valid_to: datetime | None,
    issued_at: datetime,
    retrieved_at: datetime,
    model: str,
    dataset: str,
    issue_time_basis: str = "published_at",
) -> ForecastRead:
    return ForecastRead(
        metric_type=metric_type,
        series_key=series_key,
        value=value,
        unit=unit,
        valid_from=valid_from,
        valid_to=valid_to,
        issued_at=issued_at,
        published_at=None if issue_time_basis == "retrieved_at" else issued_at,
        retrieved_at=retrieved_at,
        source_id=source_id,
        source_record_id=f"{source_id}:{metric_type}:{valid_from.isoformat()}",
        model_name=model,
        attributes={
            "classification": "forecast",
            "dataset": dataset,
            "issueTimeBasis": issue_time_basis,
        },
    )


def _notice(
    *,
    kind: str,
    published_at: datetime,
    retrieved_at: datetime,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> ReportedNoticeRead:
    is_warning = kind == "system_warning"
    return ReportedNoticeRead(
        id=f"row:{kind}",
        source_id="elexon.syswarn" if is_warning else "elexon.remit",
        notice_kind=kind,
        external_id=f"external:{kind}",
        revision_key=f"revision:{kind}:1",
        revision_number=None if is_warning else 1,
        published_at=published_at,
        retrieved_at=retrieved_at,
        event_start=starts_at,
        event_end=ends_at,
        heading=None if is_warning else "REMIT Information",
        event_type=None if is_warning else "Unavailability",
        event_status=None if is_warning else "Active",
        affected_unit=None if is_warning else "Example Unit 1",
        asset_id=None if is_warning else "asset-1",
        fuel_type=None if is_warning else "gas",
        normal_capacity_mw=None if is_warning else 600,
        available_capacity_mw=None if is_warning else 100,
        unavailable_capacity_mw=None if is_warning else 500,
        reported_cause=None,
        reported_related_information=None,
        warning_type="System Warning" if is_warning else None,
        warning_text="A system warning has been reported." if is_warning else None,
        evidence={"classification": "reported"},
    )


async def test_full_briefing_assembles_bounded_deterministic_sections() -> None:
    repository = FullBriefingRepository()

    first = await present_today_briefing(repository, as_of=NOW)
    second = await present_today_briefing(repository, as_of=NOW)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.coverage.status is BriefingStatus.COMPLETE
    assert first.now.status is CurrentPositionStatus.COMPLETE
    assert [value.metric_id for value in first.now.values] == [
        "demand",
        "carbon",
        "frequency",
    ]
    assert {change.metric_id for change in first.changes} == {
        "demand",
        "carbon",
        "frequency",
    }
    assert len(first.comparison_periods) == 3
    assert {moment.label for moment in first.next_moments} == {
        "Lowest national carbon forecast",
        "National demand forecast peak",
        "National wind forecast peak",
    }
    assert first.best_window is not None
    assert first.best_window.end - first.best_window.start == timedelta(minutes=60)
    assert first.best_window.coverage_fraction == 1.0
    assert first.best_window.average_value == 45
    assert first.reported_events.total_count == 2
    assert {event.timing for event in first.reported_events.items} == {
        ReportedEventTiming.ACTIVE,
        ReportedEventTiming.UPCOMING,
    }
    assert first.revision_watermark.observed_through <= NOW
    assert first.revision_watermark.forecast_captured_through <= NOW
    assert first.revision_watermark.reported_through <= NOW
    assert all(status.state is SourceState.LIVE for status in first.source_statuses)

    public_text = first.model_dump_json(by_alias=True).casefold()
    assert "because" not in public_text
    assert "saving" not in public_text
    assert "saved" not in public_text
    assert "cost" not in public_text


async def test_partial_forecast_failure_preserves_current_changes_and_events() -> None:
    briefing = await present_today_briefing(
        PartialBriefingRepository(),
        as_of=NOW,
    )

    assert briefing.coverage.status is BriefingStatus.PARTIAL
    assert briefing.now.status is CurrentPositionStatus.COMPLETE
    assert len(briefing.changes) == 3
    assert briefing.reported_events.items
    assert briefing.next_moments == []
    assert briefing.best_window is None
    assert "forecast.carbon" in briefing.coverage.missing_families
    assert "best_window" in briefing.coverage.missing_families
    assert any("sections were retained" in note for note in briefing.coverage.notes)


async def test_no_data_returns_typed_offline_briefing_instead_of_error() -> None:
    briefing = await present_today_briefing(
        EmptyBriefingRepository(),
        as_of=NOW,
    )

    assert briefing.coverage.status is BriefingStatus.OFFLINE
    assert briefing.now.status is CurrentPositionStatus.UNAVAILABLE
    assert briefing.now.values == []
    assert briefing.changes == []
    assert briefing.next_moments == []
    assert briefing.reported_events.items == []
    assert briefing.best_window is None
    assert briefing.source_statuses
    assert all(
        status.state is SourceState.UNAVAILABLE
        for status in briefing.source_statuses
    )


async def test_one_hour_changes_require_same_source_and_series_identity() -> None:
    briefing = await present_today_briefing(
        IncompatiblePreviousRepository(),
        as_of=NOW,
    )

    assert {change.metric_id for change in briefing.changes} == {
        "carbon",
        "frequency",
    }
    assert all(change.metric_id != "demand" for change in briefing.changes)


async def test_display_period_remains_dst_safe_at_clock_change() -> None:
    instant = datetime(2026, 10, 25, 1, 15, tzinfo=UTC)

    briefing = await present_today_briefing(
        EmptyBriefingRepository(),
        as_of=instant,
    )

    assert briefing.display_period.timezone == "Europe/London"
    assert briefing.display_period.local_date.isoformat() == "2026-10-25"
    assert briefing.display_period.starts_at.utcoffset() is not None
    assert briefing.display_period.ends_at > briefing.display_period.starts_at


def test_briefing_route_is_camel_case_etagged_and_cached_for_60_seconds() -> None:
    app.dependency_overrides[get_grid_read_repository] = lambda: FullBriefingRepository()
    try:
        with TestClient(app) as client:
            first = client.get("/v1/briefing/today")
            second = client.get(
                "/v1/briefing/today",
                headers={"If-None-Match": first.headers["etag"]},
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.headers["cache-control"].startswith("public, max-age=60")
    assert second.status_code == 304
    payload = first.json()
    assert "generatedAt" in payload
    assert "generated_at" not in payload
    assert "displayPeriod" in payload
    assert "sourceStatuses" in payload
    assert "stableId" in payload["now"]["values"][0]
    assert "sourceIds" in payload["now"]["values"][0]
