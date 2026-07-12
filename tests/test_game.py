from datetime import UTC, date, datetime, timedelta

from app.game.models import PredictionResolutionState
from app.game.resolution import build_prediction_resolution
from app.game.service import build_daily_game
from app.persistence.reads import InterconnectorRead, ReadProvenance


def test_stale_data_disables_missions_and_prediction() -> None:
    game = build_daily_game(
        now=datetime(2026, 7, 11, 12, tzinfo=UTC),
        source_fresh=False,
        has_forecast=True,
        has_events=True,
    )
    assert game.prediction is None
    assert all(not mission.available for mission in game.missions)


def test_fresh_day_has_deterministic_prediction_window() -> None:
    game = build_daily_game(
        now=datetime(2026, 7, 11, 9, tzinfo=UTC),
        source_fresh=True,
        has_forecast=True,
        has_events=False,
    )
    assert game.prediction is not None
    assert game.prediction.metric == "net_interconnector_flow_mw"
    assert game.prediction.locks_at < game.prediction.resolves_from


DAY = date(2026, 7, 11)
TARGET = datetime(2026, 7, 11, 17, tzinfo=UTC)  # 18:00 Europe/London


def flow(
    connector: str,
    megawatts: float,
    *,
    observed_at: datetime = TARGET,
    retrieved_at: datetime | None = None,
    source_id: str = "elexon.fuelinst",
    source_record_id: str | None = None,
    revision: int = 0,
) -> InterconnectorRead:
    return InterconnectorRead(
        connector_id=connector,
        display_name=connector,
        counterparty="Elsewhere",
        megawatts=megawatts,
        provenance=ReadProvenance(
            source_id=source_id,
            source_record_id=source_record_id or f"{connector}:{megawatts}",
            observed_at=observed_at,
            published_at=observed_at + timedelta(minutes=1),
            retrieved_at=retrieved_at or observed_at + timedelta(minutes=2),
            revision=revision,
        ),
    )


def test_prediction_stays_pending_until_the_complete_evidence_window_closes() -> None:
    result = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=4, seconds=59),
        interconnectors=(flow("IFA", 900),),
    )

    assert result.state is PredictionResolutionState.PENDING
    assert result.outcome is None
    assert result.observed_value_mw is None
    assert result.resolution_revision == 0
    assert result.evidence_checksum


def test_complete_positive_snapshot_resolves_importing_with_provenance() -> None:
    result = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(
            flow("IFA", 900),
            flow("NEMO", -200),
            flow("NSL", 450),
        ),
    )

    assert result.state == "resolved"
    assert result.outcome == "importing"
    assert result.observed_value_mw == 1_150
    assert result.observed_at == TARGET
    assert result.coverage.complete is True
    assert result.coverage.coverage_fraction == 1
    assert result.source_ids == ["elexon.fuelinst"]
    assert len(result.source_record_ids) == 3
    assert "positive signed net flow" in result.reason
    payload = result.model_dump(mode="json", by_alias=True)
    assert payload["predictionID"] == "2026-07-11:energy-position-1800"
    assert payload["observedValueMW"] == 1_150
    assert payload["sourceIDs"] == ["elexon.fuelinst"]


def test_complete_negative_snapshot_resolves_exporting() -> None:
    result = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(flow("IFA", -800), flow("NEMO", 100)),
    )

    assert result.state == "resolved"
    assert result.outcome == "exporting"
    assert result.observed_value_mw == -700


def test_near_balanced_snapshot_is_void_not_forced_into_a_choice() -> None:
    result = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(flow("IFA", 100), flow("NEMO", -75)),
    )

    assert result.state == "void"
    assert result.outcome is None
    assert result.observed_value_mw == 25
    assert result.coverage.complete is True
    assert "±50 MW" in result.reason


def test_missing_or_partial_same_timestamp_evidence_is_void() -> None:
    missing = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
    )
    partial = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(
            flow("IFA", 500, observed_at=TARGET - timedelta(minutes=1)),
            flow("NEMO", 300, observed_at=TARGET + timedelta(minutes=1)),
        ),
    )

    assert missing.state == "void"
    assert missing.coverage.expected_connector_count == 0
    assert partial.state == "void"
    assert partial.coverage.expected_connector_count == 2
    assert partial.coverage.observed_connector_count == 1
    assert partial.coverage.coverage_fraction == 0.5


def test_nearest_complete_snapshot_wins_and_latest_revision_is_selected() -> None:
    older = TARGET - timedelta(minutes=2)
    nearer = TARGET + timedelta(minutes=1)
    result = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(
            flow("IFA", -900, observed_at=older),
            flow("NEMO", -100, observed_at=older),
            flow(
                "IFA",
                200,
                observed_at=nearer,
                retrieved_at=nearer + timedelta(minutes=1),
                source_record_id="ifa-old-revision",
            ),
            flow(
                "IFA",
                800,
                observed_at=nearer,
                retrieved_at=nearer + timedelta(minutes=3),
                source_record_id="ifa-new-revision",
            ),
            flow("NEMO", 100, observed_at=nearer),
        ),
    )

    assert result.observed_at == nearer
    assert result.observed_value_mw == 900
    assert result.source_record_ids == ["NEMO:100", "ifa-new-revision"]
    assert "ifa-old-revision" not in result.source_record_ids


def test_unchanged_repoll_does_not_manufacture_a_correction_checksum() -> None:
    first = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(
            flow("IFA", 700, retrieved_at=TARGET + timedelta(minutes=2)),
            flow("NEMO", -100, retrieved_at=TARGET + timedelta(minutes=2)),
        ),
    )
    repolled = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(hours=1),
        interconnectors=(
            flow("IFA", 700, retrieved_at=TARGET + timedelta(minutes=50)),
            flow("NEMO", -100, retrieved_at=TARGET + timedelta(minutes=50)),
        ),
    )

    assert repolled.revision_watermark_at != first.revision_watermark_at
    assert repolled.observed_value_mw == first.observed_value_mw
    assert repolled.source_revision_keys == first.source_revision_keys
    assert repolled.evidence_checksum == first.evidence_checksum


def test_explicit_publisher_revision_wins_even_if_it_was_retrieved_earlier() -> None:
    result = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(
            flow(
                "IFA",
                -500,
                revision=1,
                retrieved_at=TARGET + timedelta(minutes=4),
                source_record_id="ifa-r1",
            ),
            flow(
                "IFA",
                700,
                revision=2,
                retrieved_at=TARGET + timedelta(minutes=3),
                source_record_id="ifa-r2",
            ),
        ),
    )

    assert result.outcome == "importing"
    assert result.observed_value_mw == 700
    assert result.source_record_ids == ["ifa-r2"]
    assert result.source_revision_keys == [
        "elexon.fuelinst:IFA:2026-07-11T17:00:00+00:00:r2"
    ]


def test_london_rule_keeps_1800_local_across_dst() -> None:
    winter = build_prediction_resolution(
        date(2026, 1, 15),
        as_of=datetime(2026, 1, 15, 18, 6, tzinfo=UTC),
    )
    summer = build_prediction_resolution(
        date(2026, 7, 15),
        as_of=datetime(2026, 7, 15, 17, 6, tzinfo=UTC),
    )

    assert winter.target_at == datetime(2026, 1, 15, 18, tzinfo=UTC)
    assert summer.target_at == datetime(2026, 7, 15, 17, tzinfo=UTC)
