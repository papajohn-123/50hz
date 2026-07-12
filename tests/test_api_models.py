from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.api.classification import (
    balance_label,
    build_headline,
    cleanliness_label,
    energy_position_label,
)
from app.api.models import FactClass, FuelReading, GridTimelineSample


NOW = datetime(2026, 7, 11, 12, tzinfo=UTC)


def test_energy_position_uses_positive_import_convention() -> None:
    assert energy_position_label(1_200) == "Net importing"
    assert energy_position_label(-1_200) == "Net exporting"
    assert energy_position_label(0) == "Near-neutral flows"


def test_headline_labels_do_not_claim_cleanliness_or_formal_balance() -> None:
    assert cleanliness_label(84) == "Lower carbon"
    assert cleanliness_label(150) == "Typical carbon"
    assert cleanliness_label(250) == "Higher carbon"
    assert balance_label(50.02) == "Frequency near 50 Hz"
    assert balance_label(49.84) == "Frequency away from 50 Hz"
    assert balance_label(None) == "Frequency unavailable"
    assert balance_label(50.0, active_system_warning=True) == "System warning"


def test_headline_copy_is_constructed_from_facts() -> None:
    headline = build_headline(
        carbon_intensity=84,
        frequency_hz=50.02,
        net_import_mw=-2_300,
        generation_mw={"wind": 16_400, "gas": 8_000, "nuclear": 6_000},
        demand_mw=38_400,
    )
    assert headline.cleanliness == "Lower carbon"
    assert headline.energy_position == "Net exporting"
    assert "Wind" in headline.interpretation
    assert "largest displayed supply component" in headline.interpretation
    assert "partial mix" in headline.interpretation
    assert "2.3 GW" in headline.interpretation


def test_imports_are_described_as_supply_not_generation() -> None:
    headline = build_headline(
        carbon_intensity=120,
        frequency_hz=50.0,
        net_import_mw=4_000,
        generation_mw={"imports": 4_000, "wind": 3_000, "gas": 2_000},
        demand_mw=20_000,
    )

    assert headline.energy_position == "Net importing"
    assert "Imports are the largest displayed supply component" in headline.interpretation
    assert "generation source" not in headline.interpretation


def test_forecast_timeline_rejects_frequency_value() -> None:
    with pytest.raises(ValidationError):
        GridTimelineSample(
            timestamp=NOW + timedelta(hours=1),
            fact_class=FactClass.FORECAST,
            demand_mw=40_000,
            carbon_intensity=120,
            frequency_hz=50.0,
            generation=[
                FuelReading(
                    fuel="wind",
                    megawatts=10_000,
                    share=25,
                    rank=1,
                    fact_class=FactClass.FORECAST,
                )
            ],
        )
