from datetime import UTC, datetime, timedelta

from app.api.models import MobileFreshness
from app.api.presenter import present_current, present_timeline
from app.api.status import present_data_status
from app.persistence.reads import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    FrequencyRead,
    ForecastRead,
    GenerationRead,
    GridTimelineRead,
    InterconnectorRead,
    ReadProvenance,
    SourceMetadataRead,
)


NOW = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
OBSERVED = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def provenance(source_id: str, observed_at: datetime = OBSERVED) -> ReadProvenance:
    return ReadProvenance(source_id, f"{source_id}:1", observed_at, observed_at, NOW)


def source(source_id: str, dataset: str, cadence_seconds: int = 300) -> SourceMetadataRead:
    return SourceMetadataRead(
        source_id,
        source_id.split(".")[0],
        dataset,
        source_id,
        None,
        None,
        None,
        cadence_seconds,
    )


def test_present_current_aggregates_fuels_and_preserves_import_sign() -> None:
    read = CurrentGridRead(
        requested_at=NOW,
        generation=(
            GenerationRead("CCGT", "gas", 2_000, provenance("elexon.fuelinst")),
            GenerationRead("OCGT", "gas", 500, provenance("elexon.fuelinst")),
            GenerationRead("WIND", "wind", 4_000, provenance("elexon.fuelinst")),
        ),
        demand=DemandRead("gb", "indo", 7_000, provenance("elexon.indo")),
        frequency=FrequencyRead("gb", 50.01, provenance("elexon.freq")),
        interconnectors=(InterconnectorRead("INTFR", "IFA", "France", 700, provenance("elexon.fuelinst")),),
        carbon=CarbonRead("GB", 84, "low", (), provenance("neso.carbon-national")),
        sources=(
            source("elexon.fuelinst", "FUELINST"),
            source("elexon.indo", "INDO"),
            source("elexon.freq", "FREQ"),
            source("neso.carbon-national", "CARBON"),
        ),
    )
    snapshot = present_current(
        read,
        previous_generation_mw={"gas": 2_000, "wind": 3_500, "imports": 500},
    )
    gas = next(reading for reading in snapshot.generation if reading.fuel == "gas")
    imports = next(reading for reading in snapshot.generation if reading.fuel == "imports")
    assert gas.megawatts == 2_500
    assert gas.change_one_hour == 500
    assert imports.change_one_hour == 200
    assert snapshot.interconnectors[0].megawatts == 700
    assert snapshot.interconnectors[0].country_code == "FR"
    assert snapshot.freshness is MobileFreshness.LIVE
    assert abs(sum(item.share for item in snapshot.generation) - 1) < 0.00001

    statuses = {status.family.value: status for status in snapshot.data_status}
    assert set(statuses) == {
        "generation",
        "demand",
        "frequency",
        "interconnectors",
        "carbon",
    }
    assert {
        family
        for family, status in statuses.items()
        if status.required_for_snapshot
    } == {"generation", "demand", "carbon"}
    generation_status = statuses["generation"]
    assert generation_status.delivery_state.value == "healthy"
    assert generation_status.fact_state.value == "live"
    assert generation_status.evaluated_at == NOW
    assert generation_status.observed_at == OBSERVED
    assert generation_status.published_at == OBSERVED
    assert generation_status.retrieved_at == NOW
    assert generation_status.valid_to == OBSERVED + timedelta(minutes=5)
    assert generation_status.observation_age_seconds == 300
    assert generation_status.retrieval_age_seconds == 0
    assert generation_status.source_ids == ["elexon.fuelinst"]
    assert generation_status.source_record_ids == ["elexon.fuelinst:1"]
    assert generation_status.series_count == 3

    assert snapshot.supply is not None
    assert snapshot.supply.is_complete is False
    assert snapshot.supply.generation_data_available is True
    assert snapshot.supply.interconnector_data_available is True
    assert snapshot.supply.domestic_generation_mw == 6_500
    assert snapshot.supply.gross_imports_mw == 700
    assert snapshot.supply.gross_exports_mw == 0
    assert snapshot.supply.net_imports_mw == 700
    assert snapshot.supply.storage_charging_mw is None
    assert snapshot.supply.legacy_displayed_generation_mw == 7_200


def test_half_hour_interval_facts_remain_live_during_publication_lag() -> None:
    recent = NOW - timedelta(minutes=2)
    interval_start = NOW - timedelta(minutes=50)
    read = CurrentGridRead(
        requested_at=NOW,
        generation=(GenerationRead("WIND", "wind", 4_000, provenance("elexon.fuelinst", recent)),),
        demand=DemandRead("gb", "indo", 7_000, provenance("elexon.indo", interval_start)),
        frequency=FrequencyRead("gb", 50.01, provenance("elexon.freq", recent)),
        interconnectors=(),
        carbon=CarbonRead("GB", 84, "low", (), provenance("neso.carbon-national", interval_start)),
        sources=(
            source("elexon.fuelinst", "FUELINST", 120),
            source("elexon.indo", "INDO", 1_800),
            source("elexon.freq", "FREQ", 60),
            source("neso.carbon-national", "CARBON", 1_800),
        ),
    )

    snapshot = present_current(read)

    assert snapshot.freshness is MobileFreshness.LIVE
    statuses = {status.family.value: status for status in snapshot.data_status}
    assert statuses["demand"].fact_state.value == "delayed"
    assert statuses["carbon"].fact_state.value == "delayed"
    assert statuses["demand"].delivery_state.value == "healthy"
    assert statuses["carbon"].delivery_state.value == "healthy"
    assert statuses["interconnectors"].fact_state.value == "unavailable"
    assert statuses["interconnectors"].observed_at is None
    assert snapshot.supply is not None
    assert snapshot.supply.interconnector_data_available is False
    assert snapshot.supply.is_complete is False


def test_supply_accounting_keeps_gross_flows_and_storage_limits_explicit() -> None:
    read = CurrentGridRead(
        requested_at=NOW,
        generation=(
            GenerationRead("WIND", "wind", 4_000, provenance("elexon.fuelinst")),
            GenerationRead("PS-GEN", "pumped_storage", 200, provenance("elexon.fuelinst")),
            GenerationRead("PS-NEG", "pumped_storage", -100, provenance("elexon.fuelinst")),
        ),
        demand=DemandRead("gb", "indo", 7_000, provenance("elexon.indo")),
        frequency=None,
        interconnectors=(
            InterconnectorRead(
                "INTFR",
                "IFA",
                "France",
                700,
                provenance("elexon.fuelinst"),
            ),
            InterconnectorRead(
                "INTNED",
                "BritNed",
                "Netherlands",
                -300,
                provenance("elexon.fuelinst"),
            ),
        ),
        carbon=CarbonRead(
            "GB",
            84,
            "low",
            (),
            provenance("neso.carbon-national"),
        ),
        sources=(
            source("elexon.fuelinst", "FUELINST"),
            source("elexon.indo", "INDO", 1_800),
            source("neso.carbon-national", "CARBON", 1_800),
        ),
    )

    snapshot = present_current(read)

    assert snapshot.supply is not None
    assert snapshot.supply.domestic_generation_mw == 4_200
    assert snapshot.supply.storage_generation_mw == 200
    assert snapshot.supply.storage_charging_mw is None
    assert snapshot.supply.gross_imports_mw == 700
    assert snapshot.supply.gross_exports_mw == 300
    assert snapshot.supply.net_imports_mw == 400
    assert snapshot.supply.legacy_displayed_generation_mw == 4_600
    assert snapshot.supply.is_complete is False
    assert "not a complete Great Britain supply balance" in snapshot.supply.note
    assert "storageChargingMW is unavailable" in snapshot.supply.note
    imports = next(item for item in snapshot.generation if item.fuel == "imports")
    storage = next(item for item in snapshot.generation if item.fuel == "storage")
    assert imports.megawatts == 400
    assert storage.megawatts == 200


def test_delivery_state_is_independent_from_fact_state() -> None:
    recent_fact_delayed_delivery = ReadProvenance(
        source_id="elexon.fuelinst",
        source_record_id="elexon.fuelinst:delayed-delivery",
        observed_at=NOW - timedelta(minutes=8),
        published_at=NOW - timedelta(minutes=7),
        retrieved_at=NOW - timedelta(minutes=6),
    )
    read = CurrentGridRead(
        requested_at=NOW,
        generation=(
            GenerationRead(
                "WIND",
                "wind",
                4_000,
                recent_fact_delayed_delivery,
            ),
        ),
        demand=DemandRead("gb", "indo", 7_000, provenance("elexon.indo")),
        frequency=None,
        interconnectors=(),
        carbon=CarbonRead(
            "GB",
            84,
            "low",
            (),
            provenance("neso.carbon-national"),
        ),
        sources=(
            source("elexon.fuelinst", "FUELINST"),
            source("elexon.indo", "INDO", 1_800),
            source("neso.carbon-national", "CARBON", 1_800),
        ),
    )

    generation = next(
        status
        for status in present_current(read).data_status
        if status.family.value == "generation"
    )

    assert generation.delivery_state.value == "delayed"
    assert generation.fact_state.value == "live"
    assert generation.retrieval_age_seconds == 360
    assert generation.observation_age_seconds == 480


def test_data_status_marks_old_and_missing_families_explicitly() -> None:
    old = ReadProvenance(
        source_id="elexon.fuelinst",
        source_record_id="elexon.fuelinst:old",
        observed_at=NOW - timedelta(minutes=16),
        published_at=NOW - timedelta(minutes=15),
        retrieved_at=NOW - timedelta(minutes=11),
    )
    read = CurrentGridRead(
        requested_at=NOW,
        generation=(GenerationRead("WIND", "wind", 4_000, old),),
        demand=None,
        frequency=None,
        interconnectors=(),
        carbon=None,
        sources=(),
    )

    statuses = {
        status.family.value: status for status in present_data_status(read)
    }

    assert statuses["generation"].delivery_state.value == "stale"
    assert statuses["generation"].fact_state.value == "stale"
    assert statuses["generation"].valid_to == (
        old.observed_at + timedelta(minutes=5)
    )
    for family in ("demand", "frequency", "interconnectors", "carbon"):
        assert statuses[family].delivery_state.value == "unavailable"
        assert statuses[family].fact_state.value == "unavailable"
        assert statuses[family].series_count == 0
        assert statuses[family].observed_at is None


def test_timeline_requires_matching_demand_and_carbon_before_showing_forecast() -> None:
    forecast_time = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)

    def forecast(metric: str, series: str, value: float) -> ForecastRead:
        return ForecastRead(
            metric_type=metric,
            series_key=series,
            value=value,
            unit="MW" if metric == "demand" else "gCO2/kWh",
            valid_from=forecast_time,
            valid_to=forecast_time.replace(minute=59),
            issued_at=OBSERVED,
            published_at=OBSERVED,
            retrieved_at=NOW,
            source_id=f"source.{metric}",
            source_record_id=None,
            model_name=None,
            attributes={},
        )

    read = GridTimelineRead(
        window_start=OBSERVED,
        window_end=datetime(2026, 7, 11, 13, 30, tzinfo=UTC),
        resolution_seconds=1_800,
        generation=(),
        demand=(),
        frequency=(),
        interconnectors=(),
        carbon=(),
        sources=(),
        forecasts=(
            forecast("demand", "n", 28_500),
            forecast("carbon_intensity", "GB", 72),
            ForecastRead(
                metric_type="generation",
                series_key="wind",
                value=7_200,
                unit="MW",
                valid_from=forecast_time,
                valid_to=None,
                issued_at=OBSERVED,
                published_at=OBSERVED,
                retrieved_at=NOW,
                source_id="elexon.windfor",
                source_record_id=None,
                model_name=None,
                attributes={"fuelType": "wind"},
            ),
        ),
    )

    timeline = present_timeline(read, now_boundary=NOW)

    assert len(timeline.samples) == 1
    sample = timeline.samples[0]
    assert sample.timestamp == forecast_time
    assert sample.fact_class.value == "forecast"
    assert sample.demand_mw == 28_500
    assert sample.carbon_intensity == 72
    assert sample.frequency_hz is None
    assert sample.generation == []
