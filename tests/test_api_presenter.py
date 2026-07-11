from datetime import UTC, datetime

from app.api.models import MobileFreshness
from app.api.presenter import present_current
from app.persistence.reads import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    FrequencyRead,
    GenerationRead,
    InterconnectorRead,
    ReadProvenance,
    SourceMetadataRead,
)


NOW = datetime(2026, 7, 11, 12, 5, tzinfo=UTC)
OBSERVED = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def provenance(source_id: str) -> ReadProvenance:
    return ReadProvenance(source_id, f"{source_id}:1", OBSERVED, OBSERVED, NOW)


def source(source_id: str, dataset: str) -> SourceMetadataRead:
    return SourceMetadataRead(source_id, source_id.split(".")[0], dataset, source_id, None, None, None, 300)


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
    snapshot = present_current(read)
    gas = next(reading for reading in snapshot.generation if reading.fuel == "gas")
    assert gas.megawatts == 2_500
    assert snapshot.interconnectors[0].megawatts == 700
    assert snapshot.interconnectors[0].country_code == "FR"
    assert snapshot.freshness is MobileFreshness.LIVE
    assert abs(sum(item.share for item in snapshot.generation) - 1) < 0.00001
