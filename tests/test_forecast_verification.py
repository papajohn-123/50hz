from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.forecast_verification.api import present_forecast_verification
from app.forecast_verification.core import (
    HORIZON_BUCKETS,
    TARGET_BY_METRIC,
    VERIFICATION_METHODOLOGY_VERSION,
    VERIFICATION_REGISTRY_VERSION,
    ForecastEvidence,
    HorizonBucket,
    OutturnEvidence,
    VerificationMetric,
    verify_forecasts,
)
from app.forecast_verification.job import (
    DEFAULT_VERIFICATION_DAYS,
    MAX_VERIFICATION_DAYS,
    VerificationDateRange,
    build_parser,
    conservative_carbon_forecast_row_bound,
    request_from_args,
    _pair_values,
    _result_values,
)
from app.forecast_verification.repository import MAX_FORECAST_INPUT_ROWS
from app.forecast_contract import (
    CAPTURE_TIME_ISSUE_BASIS,
    NATIONAL_FORECAST_METHODOLOGY_VERSION,
    SOURCE_ISSUE_TIME_UNAVAILABLE,
)
from app.api.forecast_vintages import (
    NATIONAL_FORECAST_METHODOLOGY_VERSION as VINTAGE_METHODOLOGY_VERSION,
)


START = datetime(2026, 7, 1, tzinfo=UTC)
END = datetime(2026, 7, 8, tzinfo=UTC)
DEMAND = TARGET_BY_METRIC[VerificationMetric.NATIONAL_DEMAND]
CARBON = TARGET_BY_METRIC[VerificationMetric.NATIONAL_CARBON_INTENSITY]


def forecast(
    valid_from: datetime,
    *,
    horizon_hours: float = 1,
    value: float = 110,
    revision: int = 0,
    issued_at: datetime | None = None,
    captured_at: datetime | None = None,
    observation_id: UUID | None = None,
) -> ForecastEvidence:
    issue = issued_at or valid_from - timedelta(hours=horizon_hours)
    return ForecastEvidence(
        observation_id=observation_id or uuid4(),
        valid_from=valid_from,
        issued_at=issue,
        captured_at=captured_at or issue + timedelta(minutes=1),
        value=value,
        revision=revision,
    )


def outturn(
    observed_at: datetime,
    *,
    value: float = 100,
    revision: int = 0,
    observation_id: UUID | None = None,
) -> OutturnEvidence:
    return OutturnEvidence(
        observation_id=observation_id or uuid4(),
        observed_at=observed_at,
        retrieved_at=observed_at + timedelta(minutes=20, days=revision),
        value=value,
        revision=revision,
    )


def test_exact_horizon_boundaries_are_non_overlapping_and_include_48_hours() -> None:
    valid = START + timedelta(days=2)
    forecasts = tuple(
        forecast(valid, horizon_hours=hours, value=100 + hours)
        for hours in (1, 3, 12, 24, 48, 49)
    )

    bundle = verify_forecasts(
        DEMAND,
        forecasts=forecasts,
        outturns=(outturn(valid),),
        window_start=valid,
        window_end=valid + timedelta(minutes=30),
    )

    assert [item.horizon for item in bundle.pairs] == [
        *HORIZON_BUCKETS,
        HorizonBucket.TWENTY_FOUR_TO_FORTY_EIGHT_HOURS,
    ]
    assert [item.forecast.value for item in bundle.pairs] == [101, 103, 112, 148, 124]
    assert [result.expected_sample_count for result in bundle.results] == [1, 1, 1, 2]


def test_every_vintage_is_paired_and_latest_unknown_correction_revision_wins() -> None:
    valid = START + timedelta(days=1)
    issue = valid - timedelta(hours=2)
    old = forecast(valid, issued_at=issue, value=90, revision=0)
    correction = forecast(valid, issued_at=issue, value=105, revision=27)
    later_vintage = forecast(valid, horizon_hours=1, value=108, revision=0)
    old_outturn = outturn(valid, value=100, revision=0)
    corrected_outturn = outturn(valid, value=101, revision=19)

    bundle = verify_forecasts(
        DEMAND,
        forecasts=(old, correction, later_vintage),
        outturns=(old_outturn, corrected_outturn),
        window_start=START,
        window_end=END,
    )

    assert [pair.forecast for pair in bundle.pairs] == [correction, later_vintage]
    assert all(pair.outturn == corrected_outturn for pair in bundle.pairs)
    assert [pair.signed_error for pair in bundle.pairs] == [4, 7]
    assert all(pair.content_sha256 != "0" * 64 for pair in bundle.pairs)
    assert bundle.results[0].expected_sample_count == 2


def test_source_published_history_can_be_captured_later_but_retrieval_basis_cannot() -> None:
    valid = START + timedelta(days=1)
    historical = forecast(
        valid,
        issued_at=valid - timedelta(hours=4),
        captured_at=valid + timedelta(days=30),
    )

    demand = verify_forecasts(
        DEMAND,
        forecasts=(historical,),
        outturns=(outturn(valid),),
        window_start=valid,
        window_end=valid + timedelta(minutes=30),
    )
    carbon = verify_forecasts(
        CARBON,
        forecasts=(historical,),
        outturns=(outturn(valid),),
        window_start=valid,
        window_end=valid + timedelta(minutes=30),
    )

    assert len(demand.pairs) == 1
    assert len(carbon.pairs) == 0
    assert carbon.results[0].reason == "no_forecasts"


def test_carbon_registry_reuses_persisted_capture_vintage_contract_and_locks() -> None:
    assert (
        CARBON.forecast_methodology_version
        == NATIONAL_FORECAST_METHODOLOGY_VERSION
        == VINTAGE_METHODOLOGY_VERSION
    )
    assert CARBON.issue_time_basis == SOURCE_ISSUE_TIME_UNAVAILABLE
    assert CARBON.effective_vintage_time_basis == CAPTURE_TIME_ISSUE_BASIS
    assert CARBON.forecast_source_id == "neso.carbon-intensity-national"
    assert CARBON.outturn_source_id == "neso.carbon-intensity-national"
    assert CARBON.forecast_ingestion_lock == "neso.carbon.national.forecast"
    assert CARBON.outturn_ingestion_lock == "neso.carbon.national.current"


def test_carbon_pair_persistence_never_synthesizes_a_source_issue_time() -> None:
    valid = START + timedelta(days=1)
    captured = valid - timedelta(hours=4)
    bundle = verify_forecasts(
        CARBON,
        forecasts=(
            forecast(
                valid,
                issued_at=captured,
                captured_at=captured,
                value=80,
            ),
        ),
        outturns=(outturn(valid, value=75),),
        window_start=valid,
        window_end=valid + timedelta(minutes=30),
    )

    pair = bundle.pairs[0]
    values = _pair_values(pair, revision=0)
    result_values = _result_values(
        bundle.results[0],
        revision=0,
        computed_at=valid + timedelta(days=1),
    )

    assert pair.forecast_vintage_at == captured
    assert pair.forecast_source_issued_at is None
    assert values["forecast_vintage_at"] == captured
    assert values["forecast_source_issued_at"] is None
    assert values["forecast_captured_at"] == captured
    assert values["issue_time_basis"] == SOURCE_ISSUE_TIME_UNAVAILABLE
    assert values["effective_vintage_time_basis"] == CAPTURE_TIME_ISSUE_BASIS
    assert values["forecast_methodology_version"] == (
        NATIONAL_FORECAST_METHODOLOGY_VERSION
    )
    assert result_values["issue_time_basis"] == SOURCE_ISSUE_TIME_UNAVAILABLE
    assert (
        result_values["effective_vintage_time_basis"]
        == CAPTURE_TIME_ISSUE_BASIS
    )
    assert result_values["forecast_methodology_version"] == (
        NATIONAL_FORECAST_METHODOLOGY_VERSION
    )


def test_error_statistics_require_100_points_and_90_percent_coverage() -> None:
    forecasts = tuple(
        forecast(START + timedelta(minutes=30 * index), value=110)
        for index in range(100)
    )
    outturns = tuple(
        outturn(START + timedelta(minutes=30 * index), value=100)
        for index in range(100)
    )

    available = verify_forecasts(
        DEMAND,
        forecasts=forecasts,
        outturns=outturns,
        window_start=START,
        window_end=START + timedelta(hours=50),
    ).results[0]
    insufficient = verify_forecasts(
        DEMAND,
        forecasts=forecasts,
        outturns=outturns[:89],
        window_start=START,
        window_end=START + timedelta(hours=50),
    ).results[0]

    assert available.status == "available"
    assert available.mae == 10
    assert available.bias == 10
    assert available.wape_percent == 10
    assert available.coverage_fraction == 1
    assert insufficient.status == "insufficient_data"
    assert insufficient.reason == "sample_and_coverage_thresholds_not_met"
    assert insufficient.coverage_fraction == 0.89


def test_safe_wape_denominator_is_explicitly_unavailable_at_zero() -> None:
    valid = START + timedelta(hours=1)
    result = verify_forecasts(
        DEMAND,
        forecasts=(forecast(valid, value=1),),
        outturns=(outturn(valid, value=0),),
        window_start=valid,
        window_end=valid + timedelta(minutes=30),
    ).results[0]

    assert result.mae == 1
    assert result.bias == 1
    assert result.wape_percent is None


@pytest.mark.parametrize(
    ("day", "expected_hours"),
    ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)),
)
def test_verification_date_range_is_london_dst_safe(day: date, expected_hours: int) -> None:
    date_range = VerificationDateRange(day, day + timedelta(days=1))

    assert (date_range.end_utc - date_range.start_utc).total_seconds() == expected_hours * 3_600


def test_refresh_latest_is_a_forced_bounded_cron_path() -> None:
    request = request_from_args(
        build_parser().parse_args(["--refresh-latest", "--metric", "national_demand"]),
        today=date(2026, 7, 12),
    )

    assert request.force and request.refresh_latest
    assert request.date_range == VerificationDateRange(date(2026, 7, 5), date(2026, 7, 12))
    assert request.metrics == (VerificationMetric.NATIONAL_DEMAND,)


def test_recent_default_and_maximum_fit_the_reviewed_carbon_input_cap() -> None:
    request = request_from_args(build_parser().parse_args([]), today=date(2026, 7, 12))

    assert request.date_range.day_count == DEFAULT_VERIFICATION_DAYS == 28
    assert MAX_VERIFICATION_DAYS == 31
    assert conservative_carbon_forecast_row_bound(MAX_VERIFICATION_DAYS) < (
        MAX_FORECAST_INPUT_ROWS
    )
    assert conservative_carbon_forecast_row_bound(95) > MAX_FORECAST_INPUT_ROWS
    with pytest.raises(ValueError, match="cannot exceed 31"):
        VerificationDateRange(date(2026, 6, 10), date(2026, 7, 12))


def stored_result(**overrides):
    values = {
        "metric_id": VerificationMetric.NATIONAL_DEMAND.value,
        "horizon_bucket": HorizonBucket.ZERO_TO_THREE_HOURS.value,
        "registry_version": VERIFICATION_REGISTRY_VERSION,
        "verification_methodology_version": VERIFICATION_METHODOLOGY_VERSION,
        "forecast_source_id": DEMAND.forecast_source_id,
        "outturn_source_id": DEMAND.outturn_source_id,
        "forecast_methodology_version": DEMAND.forecast_methodology_version,
        "outturn_methodology_version": DEMAND.outturn_methodology_version,
        "issue_time_basis": DEMAND.issue_time_basis,
        "effective_vintage_time_basis": DEMAND.effective_vintage_time_basis,
        "unit": DEMAND.unit,
        "revision": 3,
        "expected_sample_count": 100,
        "verified_sample_count": 100,
        "coverage_fraction": 1.0,
        "evidence_checksum": "a" * 64,
        "window_start": START,
        "window_end": END,
        "computed_at": END + timedelta(minutes=1),
        "source_watermark_at": END,
        "status": "available",
        "reason": "eligible",
        "mae": 10.0,
        "bias": 2.0,
        "wape_percent": 4.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_public_contract_withholds_statistics_until_thresholds_are_met() -> None:
    row = stored_result(
        status="insufficient_data",
        reason="fewer_than_100_verified_samples",
        expected_sample_count=99,
        verified_sample_count=99,
        mae=10,
        bias=2,
        wape_percent=4,
    )

    response = present_forecast_verification(
        (row,), metric=VerificationMetric.NATIONAL_DEMAND
    )
    first = response.results[0]

    assert len(response.results) == 4
    assert first.status == "insufficient_data"
    assert first.mae is None and first.bias is None and first.wape_percent is None
    assert first.verified_samples == 99
    assert response.results[1].reason == "not_computed"
    assert "internal" not in response.model_dump_json(by_alias=True).casefold()


def test_public_contract_exposes_only_eligible_aggregate_statistics() -> None:
    response = present_forecast_verification(
        (stored_result(),), metric=VerificationMetric.NATIONAL_DEMAND
    )
    first = response.results[0]

    assert first.status == "available"
    assert (first.mae, first.bias, first.wape_percent) == (10, 2, 4)
    assert first.issue_time_basis == "source_published_at"
    assert first.evidence_checksum == "a" * 64
    payload = response.model_dump(by_alias=True)
    assert payload["results"][0]["forecast"]["sourceID"] == "elexon.ndf"
    assert "observationID" not in str(payload)


def test_public_carbon_contract_declares_capture_without_claiming_issue_time() -> None:
    row = stored_result(
        metric_id=VerificationMetric.NATIONAL_CARBON_INTENSITY.value,
        forecast_source_id=CARBON.forecast_source_id,
        outturn_source_id=CARBON.outturn_source_id,
        forecast_methodology_version=CARBON.forecast_methodology_version,
        outturn_methodology_version=CARBON.outturn_methodology_version,
        issue_time_basis=CARBON.issue_time_basis,
        effective_vintage_time_basis=CARBON.effective_vintage_time_basis,
        unit=CARBON.unit,
    )

    response = present_forecast_verification(
        (row,), metric=VerificationMetric.NATIONAL_CARBON_INTENSITY
    )
    first = response.results[0]
    payload = response.model_dump(by_alias=True)["results"][0]

    assert first.forecast.methodology_version == (
        NATIONAL_FORECAST_METHODOLOGY_VERSION
    )
    assert payload["issueTimeBasis"] == SOURCE_ISSUE_TIME_UNAVAILABLE
    assert payload["effectiveVintageTimeBasis"] == CAPTURE_TIME_ISSUE_BASIS


def test_public_contract_ignores_unknown_or_invalid_revisions() -> None:
    response = present_forecast_verification(
        (stored_result(revision=-1),),
        metric=VerificationMetric.NATIONAL_DEMAND,
    )

    assert all(item.reason == "not_computed" for item in response.results)
    assert response.generated_at is None
