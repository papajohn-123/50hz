from datetime import UTC, datetime, timedelta

from app.api.models import MobileFreshness
from app.api.presenter import present_current, present_timeline
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

    assert present_current(read).freshness is MobileFreshness.LIVE


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
