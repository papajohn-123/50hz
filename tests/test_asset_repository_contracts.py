from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.assets.repository import PlannedSegmentRead, _latest_settled_statement


NOW = datetime(2026, 7, 15, 11, 10, tzinfo=UTC)


def test_plan_interpolation_is_bounded_and_does_not_extrapolate() -> None:
    plan = PlannedSegmentRead(
        asset_id=UUID("de76e194-3dee-50cb-aa5f-f9c329fd3c77"),
        national_grid_bm_unit="TEST-1",
        elexon_bm_unit=None,
        settlement_date="2026-07-15",
        settlement_period=23,
        segment_start=NOW,
        segment_end=NOW + timedelta(minutes=10),
        level_from_mw=-20,
        level_to_mw=80,
        retrieved_at=NOW,
    )

    assert plan.level_at(NOW + timedelta(minutes=5)) == pytest.approx(30)
    assert plan.level_at(NOW - timedelta(seconds=1)) is None
    assert plan.level_at(NOW + timedelta(minutes=10)) is None


def test_settled_query_accepts_asset_national_or_elexon_ids_and_is_bounded() -> None:
    statement = _latest_settled_statement(
        asset_ids=(UUID("de76e194-3dee-50cb-aa5f-f9c329fd3c77"),),
        national_grid_bm_units=("TEST-1",),
        elexon_bm_units=("2__AALAB000",),
        settled_per_unit=12,
    )
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "row_number() OVER" in compiled
    assert "asset_id" in compiled
    assert "national_grid_bm_unit" in compiled
    assert "elexon_bm_unit" in compiled
    assert " OR " in compiled
    assert "interval_rank <= 12" in compiled
    assert "revision_rank = 1" in compiled
    assert "revision DESC" in compiled
    assert "settlement_date" in compiled
    assert "settlement_period" in compiled
