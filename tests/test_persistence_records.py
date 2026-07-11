from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.persistence.records import (
    canonical_source_id,
    map_frequency_record,
    map_generation_record,
    map_interconnector_record,
    source_metadata_values,
)
from app.sources.types import (
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
)


NOW = datetime(2026, 7, 11, 12, 15, tzinfo=UTC)
RAW_ID = UUID("7f0cd8f5-f9c8-465f-ae0d-24f3578ba145")


def test_source_metadata_is_dataset_scoped_and_provider_normalized() -> None:
    values = source_metadata_values(
        provider="elexon.fuelinst",
        dataset="FUELINST",
        request_url="https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST?format=json",
    )

    assert values["id"] == "elexon.fuelinst"
    assert values["provider"] == "elexon"
    assert values["dataset"] == "FUELINST"
    assert values["base_url"] == "https://data.elexon.co.uk"
    assert values["expected_cadence_seconds"] == 120
    assert values["attribution"] == "Data supplied by Elexon Limited."


def test_indo_metadata_uses_the_source_half_hour_cadence() -> None:
    values = source_metadata_values(
        provider="elexon.indo",
        dataset="INDO",
        request_url="https://data.elexon.co.uk/bmrs/api/v1/datasets/INDO/stream",
    )

    assert values["expected_cadence_seconds"] == 1_800


def test_long_source_ids_are_stable_and_bounded() -> None:
    first = canonical_source_id("provider", "x" * 100)
    second = canonical_source_id("provider", "x" * 100)

    assert first == second
    assert len(first) <= 64


def test_generation_record_maps_all_provenance_and_settlement_fields() -> None:
    record = GenerationRecord(
        source_key="elexon:FUELINST:2026-07-11T12:15:00Z:WIND",
        observed_at=NOW,
        published_at=NOW + timedelta(seconds=20),
        retrieved_at=NOW + timedelta(seconds=40),
        fuel_code="WIND",
        fuel_type="wind",
        generation_mw=13_500,
        settlement_date=date(2026, 7, 11),
        settlement_period=26,
    )

    values = map_generation_record(
        record, source_id="elexon.fuelinst", raw_payload_id=RAW_ID
    )

    assert values["series_key"] == "WIND"
    assert values["fuel_type"] == "wind"
    assert values["generation_mw"] == 13_500
    assert values["source_record_id"] == record.source_key
    assert values["raw_payload_id"] == RAW_ID
    assert values["settlement_period"] == 26
    assert values["attributes"]["dataset"] == "FUELINST"


def test_frequency_mapping_keeps_absent_publication_time() -> None:
    values = map_frequency_record(
        FrequencyRecord(
            source_key="elexon:FREQ:2026-07-11T12:15:00Z",
            observed_at=NOW,
            retrieved_at=NOW + timedelta(seconds=5),
            frequency_hz=49.987,
        ),
        source_id="elexon.freq",
        raw_payload_id=RAW_ID,
    )

    assert values["published_at"] is None
    assert values["frequency_hz"] == 49.987


@pytest.mark.parametrize(
    ("connector", "expected_counterparty"),
    [("INTELEC", "France"), ("INTNSL", "Norway")],
)
def test_interconnector_mapping_preserves_signed_flow_and_display_name(
    connector: str, expected_counterparty: str
) -> None:
    values = map_interconnector_record(
        InterconnectorFlowRecord(
            source_key=f"elexon:FUELINST:2026-07-11T12:15:00Z:{connector}",
            observed_at=NOW,
            published_at=NOW,
            retrieved_at=NOW,
            interconnector_id=connector,
            interconnector_name="Friendly connector name",
            flow_mw=-356,
        ),
        source_id="elexon.fuelinst",
        raw_payload_id=RAW_ID,
    )

    assert values["flow_mw"] == -356
    assert values["counterparty"] == expected_counterparty
    assert values["attributes"]["displayName"] == "Friendly connector name"
    assert values["attributes"]["signConvention"] == "positive_import_into_gb"


def test_mapping_rejects_naive_operational_timestamps() -> None:
    record = FrequencyRecord(
        source_key="bad",
        observed_at=datetime(2026, 7, 11, 12, 15),
        retrieved_at=NOW,
        frequency_hz=50.0,
    )

    with pytest.raises(ValueError, match="observed_at must be timezone-aware"):
        map_frequency_record(
            record, source_id="elexon.freq", raw_payload_id=RAW_ID
        )
