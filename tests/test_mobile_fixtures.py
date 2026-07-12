import json
from pathlib import Path

from app.api.models import GridSnapshotResponse, GridTimelineResponse


FIXTURES = Path(__file__).parents[1] / "ios" / "50Hz" / "Resources" / "Fixtures"


def test_native_snapshot_fixture_matches_backend_contract() -> None:
    payload = json.loads((FIXTURES / "grid_snapshot.json").read_text())
    snapshot = GridSnapshotResponse.model_validate(payload)
    assert snapshot.generation
    assert {status.family.value for status in snapshot.data_status} == {
        "generation",
        "demand",
        "frequency",
        "interconnectors",
        "carbon",
    }
    assert snapshot.supply is not None
    assert snapshot.supply.is_complete is False
    assert snapshot.supply.storage_charging_mw is None
    assert snapshot.model_dump(by_alias=True)["headline"]["energyPosition"] == "Exporting"


def test_native_timeline_fixture_matches_backend_contract() -> None:
    payload = json.loads((FIXTURES / "grid_timeline.json").read_text())
    timeline = GridTimelineResponse.model_validate(payload)
    assert any(point.fact_class.value == "forecast" for point in timeline.samples)
    assert all(point.frequency_hz is None for point in timeline.samples if point.fact_class.value == "forecast")
