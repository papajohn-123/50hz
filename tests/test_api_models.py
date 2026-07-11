from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.api.classification import build_headline, energy_position_label
from app.api.models import FactClass, FuelReading, GridTimelineSample


NOW = datetime(2026, 7, 11, 12, tzinfo=UTC)


def test_energy_position_uses_positive_import_convention() -> None:
    assert energy_position_label(1_200) == "Importing"
    assert energy_position_label(-1_200) == "Exporting"


def test_headline_copy_is_constructed_from_facts() -> None:
    headline = build_headline(
        carbon_intensity=84,
        frequency_hz=50.02,
        net_import_mw=-2_300,
        generation_mw={"wind": 16_400, "gas": 8_000, "nuclear": 6_000},
        demand_mw=38_400,
    )
    assert headline.cleanliness == "Clean"
    assert headline.energy_position == "Exporting strongly"
    assert "Wind" in headline.interpretation
    assert "2.3 GW" in headline.interpretation


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
