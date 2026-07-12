from datetime import UTC, date, datetime, timedelta

from app.game.connectors import ConnectorRegistry, connector_registry_for_date
from app.game.models import PredictionResolutionState
from app.game.resolution import build_prediction_resolution
from app.game.service import build_daily_game
from app.persistence.reads import InterconnectorRead, ReadProvenance
from app.sources.elexon import INTERCONNECTOR_NAMES


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


def registry(*connectors: str, version: str = "test-connectors-v1") -> ConnectorRegistry:
    return ConnectorRegistry(
        version=version,
        effective_from=date(2026, 1, 1),
        expected_connector_ids=connectors,
    )


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
        interconnectors=(flow("INTFR", 900),),
        connector_registry=registry("INTFR"),
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
            flow("INTFR", 900),
            flow("INTNEM", -200),
            flow("INTNSL", 450),
        ),
        connector_registry=registry("INTFR", "INTNEM", "INTNSL"),
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
        interconnectors=(flow("INTFR", -800), flow("INTNEM", 100)),
        connector_registry=registry("INTFR", "INTNEM"),
    )

    assert result.state == "resolved"
    assert result.outcome == "exporting"
    assert result.observed_value_mw == -700


def test_near_balanced_snapshot_is_void_not_forced_into_a_choice() -> None:
    result = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(flow("INTFR", 100), flow("INTNEM", -75)),
        connector_registry=registry("INTFR", "INTNEM"),
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
        connector_registry=registry("INTFR", "INTNEM"),
    )
    partial = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(
            flow("INTFR", 500, observed_at=TARGET - timedelta(minutes=1)),
            flow("INTNEM", 300, observed_at=TARGET + timedelta(minutes=1)),
        ),
        connector_registry=registry("INTFR", "INTNEM"),
    )

    assert missing.state == "void"
    assert missing.coverage.expected_connector_count == 2
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
            flow("INTFR", -900, observed_at=older),
            flow("INTNEM", -100, observed_at=older),
            flow(
                "INTFR",
                200,
                observed_at=nearer,
                retrieved_at=nearer + timedelta(minutes=1),
                source_record_id="ifa-old-revision",
            ),
            flow(
                "INTFR",
                800,
                observed_at=nearer,
                retrieved_at=nearer + timedelta(minutes=3),
                source_record_id="ifa-new-revision",
            ),
            flow("INTNEM", 100, observed_at=nearer),
        ),
        connector_registry=registry("INTFR", "INTNEM"),
    )

    assert result.observed_at == nearer
    assert result.observed_value_mw == 900
    assert result.source_record_ids == ["INTNEM:100", "ifa-new-revision"]
    assert "ifa-old-revision" not in result.source_record_ids


def test_unchanged_repoll_does_not_manufacture_a_correction_checksum() -> None:
    first = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(
            flow("INTFR", 700, retrieved_at=TARGET + timedelta(minutes=2)),
            flow("INTNEM", -100, retrieved_at=TARGET + timedelta(minutes=2)),
        ),
        connector_registry=registry("INTFR", "INTNEM"),
    )
    repolled = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(hours=1),
        interconnectors=(
            flow("INTFR", 700, retrieved_at=TARGET + timedelta(minutes=50)),
            flow("INTNEM", -100, retrieved_at=TARGET + timedelta(minutes=50)),
        ),
        connector_registry=registry("INTFR", "INTNEM"),
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
                "INTFR",
                -500,
                revision=1,
                retrieved_at=TARGET + timedelta(minutes=4),
                source_record_id="ifa-r1",
            ),
            flow(
                "INTFR",
                700,
                revision=2,
                retrieved_at=TARGET + timedelta(minutes=3),
                source_record_id="ifa-r2",
            ),
        ),
        connector_registry=registry("INTFR"),
    )

    assert result.outcome == "importing"
    assert result.observed_value_mw == 700
    assert result.source_record_ids == ["ifa-r2"]
    assert result.source_revision_keys == [
        "elexon.fuelinst:INTFR:2026-07-11T17:00:00+00:00:r2"
    ]


def test_registry_version_is_audited_in_rule_and_checksum() -> None:
    first = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(flow("INTFR", 500),),
        connector_registry=registry("INTFR", version="test-connectors-v1"),
    )
    revised_registry = build_prediction_resolution(
        DAY,
        as_of=TARGET + timedelta(minutes=6),
        interconnectors=(flow("INTFR", 500),),
        connector_registry=registry("INTFR", version="test-connectors-v2"),
    )

    assert first.connector_registry_version == "test-connectors-v1"
    assert "Rule v1" in first.rule
    assert "test-connectors-v1" in first.rule
    assert first.evidence_checksum != revised_registry.evidence_checksum


def test_effective_registry_matches_the_authoritative_elexon_code_map() -> None:
    effective = connector_registry_for_date(DAY)

    assert set(effective.expected_connector_ids) == set(INTERCONNECTOR_NAMES)
    assert effective.effective_from <= DAY


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
