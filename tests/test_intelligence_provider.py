from datetime import UTC, datetime, timedelta

import pytest

from app.api.notices import reported_notice_event_id
from app.intelligence.provider import DatabaseGridToolProvider
from app.persistence.reads import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    ForecastRead,
    FrequencyRead,
    GenerationRead,
    GridTimelineRead,
    InterconnectorRead,
    ReadProvenance,
    ReportedNoticeRead,
    SourceMetadataRead,
)


NOW = datetime(2026, 7, 11, 15, tzinfo=UTC)
OBSERVED = NOW - timedelta(minutes=4)
SOURCE = SourceMetadataRead(
    id="elexon.live",
    provider="Elexon",
    dataset="live",
    display_name="Elexon live data",
    documentation_url="https://bmrs.elexon.co.uk/api-documentation",
    licence_url=None,
    attribution="Data supplied by Elexon.",
    expected_cadence_seconds=120,
)
CARBON_SOURCE = SourceMetadataRead(
    id="neso.carbon",
    provider="NESO",
    dataset="carbon",
    display_name="NESO Carbon Intensity",
    documentation_url="https://carbonintensity.org.uk/",
    licence_url=None,
    attribution="Carbon Intensity API.",
    expected_cadence_seconds=1800,
)


def provenance(source_id: str, suffix: str) -> ReadProvenance:
    return ReadProvenance(
        source_id=source_id,
        source_record_id=f"{source_id}:{suffix}",
        observed_at=OBSERVED,
        published_at=OBSERVED,
        retrieved_at=OBSERVED + timedelta(minutes=1),
    )


class Repository:
    def __init__(self) -> None:
        self.current_as_of = None
        self.timeline_window_end = None

    async def get_current(self, *, as_of=None) -> CurrentGridRead:
        self.current_as_of = as_of
        return CurrentGridRead(
            requested_at=NOW,
            generation=(
                GenerationRead("wind-onshore", "wind", 8_000, provenance("elexon.live", "wind-1")),
                GenerationRead("wind-offshore", "wind", 4_000, provenance("elexon.live", "wind-2")),
                GenerationRead("gas", "gas", 6_500, provenance("elexon.live", "gas")),
            ),
            demand=DemandRead("gb", "indo", 28_000, provenance("elexon.live", "demand")),
            frequency=FrequencyRead("gb", 49.997, provenance("elexon.live", "frequency")),
            interconnectors=(
                InterconnectorRead("IFA", "IFA", "France", 500, provenance("elexon.live", "ifa")),
                InterconnectorRead("NSL", "NSL", "Norway", -200, provenance("elexon.live", "nsl")),
            ),
            carbon=CarbonRead("GB", 82, "low", (), provenance("neso.carbon", "carbon")),
            sources=(SOURCE, CARBON_SOURCE),
        )

    async def get_timeline(self, *, window_start, window_end, resolution_seconds):
        self.timeline_window_end = window_end
        current = await self.get_current()
        return GridTimelineRead(
            window_start=window_start,
            window_end=window_end,
            resolution_seconds=resolution_seconds,
            generation=current.generation,
            demand=(current.demand,),
            frequency=(current.frequency,),
            interconnectors=current.interconnectors,
            carbon=(current.carbon,),
            sources=current.sources,
        )

    async def list_sources(self):
        return (SOURCE, CARBON_SOURCE)

    async def get_carbon_forecast(self, *, region_code, window_start, window_end, issued_before):
        assert region_code == "13"
        return tuple(
            ForecastRead(
                metric_type="carbon_intensity",
                series_key="13",
                value=value,
                unit="gCO2/kWh",
                valid_from=NOW + timedelta(minutes=30 * index),
                valid_to=NOW + timedelta(minutes=30 * (index + 1)),
                issued_at=NOW - timedelta(minutes=10),
                published_at=NOW - timedelta(minutes=10),
                retrieved_at=NOW - timedelta(minutes=9),
                source_id="neso.carbon",
                source_record_id=f"forecast:{index}",
                model_name=None,
                attributes={},
            )
            for index, value in enumerate((120, 90, 80, 100))
        )


@pytest.mark.asyncio
async def test_current_tool_aggregates_grid_facts_with_resolvable_sources() -> None:
    provider = DatabaseGridToolProvider(
        Repository(),
        lambda: None,
        clock=lambda: NOW,
    )
    envelope = await provider.call("get_current_grid_state", {})

    wind = next(fact for fact in envelope.facts if fact.metric == "wind_mw")
    flow = next(
        fact for fact in envelope.facts
        if fact.metric == "net_interconnector_flow_mw"
    )
    assert wind.value == 12_000
    assert len(wind.source_record_ids) == 2
    assert flow.value == 300
    assert envelope.freshness == "fresh"
    assert set(envelope.source_refs) == {"elexon.live", "neso.carbon"}


@pytest.mark.asyncio
async def test_metric_tool_returns_only_the_allowlisted_requested_series() -> None:
    provider = DatabaseGridToolProvider(Repository(), lambda: None, clock=lambda: NOW)
    envelope = await provider.call(
        "get_metric_series", {"metric": "wind_mw", "hours": 12}
    )
    assert {fact.metric for fact in envelope.facts} == {"wind_mw"}
    assert envelope.facts[0].value == 12_000


@pytest.mark.asyncio
async def test_cleanest_window_is_calculated_without_model_judgement() -> None:
    provider = DatabaseGridToolProvider(Repository(), lambda: None, clock=lambda: NOW)
    envelope = await provider.call(
        "find_cleanest_window",
        {"region_code": "13", "duration_hours": 1},
    )
    intensity = next(
        fact for fact in envelope.facts
        if fact.fact_id == "cleanest_window_intensity"
    )
    start = next(
        fact for fact in envelope.facts if fact.fact_id == "cleanest_window_start"
    )
    assert intensity.value == 85
    assert start.value == (NOW + timedelta(minutes=30)).isoformat()
    assert envelope.evidence_class == "forecast"
    assert envelope.limitations


@pytest.mark.asyncio
async def test_selected_map_time_bounds_current_and_series_reads() -> None:
    repository = Repository()
    provider = DatabaseGridToolProvider(repository, lambda: None, clock=lambda: NOW)
    selected = NOW - timedelta(minutes=2)

    current = await provider.call("get_current_grid_state", {"_as_of": selected})
    assert repository.current_as_of == selected
    assert any("historical snapshot" in value for value in current.limitations)

    series = await provider.call(
        "get_metric_series",
        {"metric": "wind_mw", "hours": 12, "_as_of": selected},
    )
    assert repository.timeline_window_end == selected
    assert any("selected historical map time" in value for value in series.limitations)


@pytest.mark.asyncio
async def test_historical_event_evidence_resolves_notice_at_selected_time() -> None:
    selected = NOW - timedelta(hours=1)
    notice = ReportedNoticeRead(
        id="notice-1",
        source_id="elexon.remit",
        notice_kind="remit_unavailability",
        external_id="mrid-history-1",
        revision_key="revision-1",
        revision_number=1,
        published_at=selected - timedelta(minutes=10),
        retrieved_at=selected - timedelta(minutes=9),
        event_start=selected - timedelta(hours=1),
        event_end=selected + timedelta(hours=1),
        heading="Unit unavailability",
        event_type="Unavailability",
        event_status="Active",
        affected_unit="Historic Unit",
        asset_id="asset-history",
        fuel_type="gas",
        normal_capacity_mw=400,
        available_capacity_mw=100,
        unavailable_capacity_mw=300,
        reported_cause=None,
        reported_related_information=None,
        warning_type=None,
        warning_text=None,
        evidence={"classification": "reported"},
    )
    remit_source = SourceMetadataRead(
        id="elexon.remit",
        provider="Elexon",
        dataset="REMIT",
        display_name="Elexon REMIT notices",
        documentation_url="https://bmrs.elexon.co.uk/api-documentation",
        licence_url=None,
        attribution="Data supplied by Elexon.",
        expected_cadence_seconds=300,
    )

    class HistoricalRepository:
        requested_as_of = None

        async def get_active_notices(self, *, as_of=None):
            self.requested_as_of = as_of
            return (notice,)

        async def list_sources(self):
            return (remit_source,)

    class EmptyResult:
        def scalar_one_or_none(self):
            return None

    class EmptySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            return EmptyResult()

    repository = HistoricalRepository()
    provider = DatabaseGridToolProvider(
        repository,
        lambda: EmptySession(),
        clock=lambda: NOW,
    )
    event_id = reported_notice_event_id(notice)
    envelope = await provider.call(
        "get_event_evidence",
        {"event_id": event_id, "_as_of": selected},
    )
    assert repository.requested_as_of == selected
    assert envelope.source_refs["elexon.remit"].publisher == "Elexon"
    assert any("active at the selected historical" in item for item in envelope.limitations)
