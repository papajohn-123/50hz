from datetime import UTC, datetime

from app.game.service import build_daily_game


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
