from __future__ import annotations

from datetime import UTC, date, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator


IDENTITY_VERSION = "50hz.metric-series.v1"
HISTORY_METHODOLOGY_VERSION = "50hz.history.comparisons.v1"
DAILY_AGGREGATE_VERSION = "50hz.history.daily-mean.v1"
RESULT_VERSION = "1"
INTERVAL_MINUTES = 30
MINIMUM_COVERAGE_FRACTION = 0.95
ROLLING_DAY_COUNT = 28


class MetricSeriesIdentity(BaseModel):
    identity_version: Literal["50hz.metric-series.v1"] = IDENTITY_VERSION
    metric_id: str = Field(min_length=1)
    geography: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    fact_class: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    methodology_version: str = Field(min_length=1)


class HalfHourObservation(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    value: float = Field(allow_inf_nan=False)
    source_record_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_half_hour(self) -> "HalfHourObservation":
        start = self.start.astimezone(UTC)
        end = self.end.astimezone(UTC)
        if end - start != timedelta(minutes=INTERVAL_MINUTES):
            raise ValueError("an observation must cover exactly 30 minutes")
        if start.minute not in (0, 30) or start.second or start.microsecond:
            raise ValueError("an observation must start on a UTC half-hour boundary")
        return self


class MetricSeries(BaseModel):
    identity: MetricSeriesIdentity
    observations: list[HalfHourObservation] = Field(default_factory=list)


class SettlementInterval(BaseModel):
    settlement_date: date
    settlement_period: int = Field(ge=1, le=50)
    start: AwareDatetime
    end: AwareDatetime


class ResultStatus(StrEnum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"
    INCOMPATIBLE_SERIES = "incompatible_series"


class ResultReason(StrEnum):
    AVAILABLE = "available"
    COVERAGE_BELOW_THRESHOLD = "coverage_below_threshold"
    DUPLICATE_INTERVALS = "duplicate_intervals"
    MISSING_REFERENCE = "missing_reference"
    DUPLICATE_REFERENCE = "duplicate_reference"
    MISSING_COMPARISON = "missing_comparison"
    DUPLICATE_COMPARISON = "duplicate_comparison"
    NONEXISTENT_LOCAL_TIME = "nonexistent_local_time"
    AMBIGUOUS_LOCAL_TIME = "ambiguous_local_time"
    INCOMPATIBLE_SERIES = "incompatible_series"


class HistoryMethodology(BaseModel):
    version: Literal["50hz.history.comparisons.v1"] = (
        HISTORY_METHODOLOGY_VERSION
    )
    timezone: Literal["Europe/London"] = "Europe/London"
    interval_minutes: Literal[30] = INTERVAL_MINUTES
    minimum_coverage_fraction: Literal[0.95] = MINIMUM_COVERAGE_FRACTION
    rolling_day_count: Literal[28] = ROLLING_DAY_COUNT
    same_time_rule: str = (
        "Match the Europe/London wall-clock half-hour; for a repeated clock "
        "hour, use the occurrence with the reference UTC offset."
    )
    quartile_rule: str = "Linear interpolation at p=0.25, 0.5 and 0.75."
    percentile_rule: str = "Empirical midrank across prior valid samples."


class DailyCoverage(BaseModel):
    result_version: Literal["1"] = RESULT_VERSION
    settlement_date: date
    expected_interval_count: int = Field(ge=1, le=50)
    raw_sample_count: int = Field(ge=0)
    unique_interval_count: int = Field(ge=0)
    coverage_fraction: float = Field(ge=0, le=1)
    minimum_coverage_fraction: Literal[0.95] = MINIMUM_COVERAGE_FRACTION
    missing_starts: list[AwareDatetime] = Field(default_factory=list)
    duplicate_starts: list[AwareDatetime] = Field(default_factory=list)
    out_of_range_sample_count: int = Field(ge=0)
    is_sufficient: bool


class DailyAggregateResult(BaseModel):
    result_version: Literal["1"] = RESULT_VERSION
    methodology_version: Literal["50hz.history.daily-mean.v1"] = (
        DAILY_AGGREGATE_VERSION
    )
    status: ResultStatus
    reason: ResultReason
    identity: MetricSeriesIdentity
    settlement_date: date
    aggregate: Literal["mean"] = "mean"
    value: float | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    coverage: DailyCoverage

    @model_validator(mode="after")
    def validate_availability(self) -> "DailyAggregateResult":
        if self.status == ResultStatus.AVAILABLE:
            if self.value is None:
                raise ValueError("an available aggregate requires a value")
        elif self.value is not None or self.source_record_ids:
            raise ValueError("an unavailable aggregate cannot expose a value or samples")
        return self


class PointComparisonKind(StrEnum):
    PREVIOUS_PERIOD = "previous_period"
    SAME_TIME_YESTERDAY = "same_time_yesterday"
    SAME_TIME_SEVEN_DAYS_AGO = "same_time_seven_days_ago"


class PointComparisonResult(BaseModel):
    result_version: Literal["1"] = RESULT_VERSION
    methodology_version: Literal["50hz.history.comparisons.v1"] = (
        HISTORY_METHODOLOGY_VERSION
    )
    kind: PointComparisonKind
    status: ResultStatus
    reason: ResultReason
    reference_start: AwareDatetime
    comparison_start: AwareDatetime | None = None
    reference_value: float | None = None
    comparison_value: float | None = None
    comparison_sample_count: int = Field(default=0, ge=0)
    reference_minus_comparison: float | None = None
    percent_change_from_comparison: float | None = None
    source_record_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_availability(self) -> "PointComparisonResult":
        if self.status == ResultStatus.AVAILABLE:
            required = (
                self.comparison_start,
                self.reference_value,
                self.comparison_value,
                self.reference_minus_comparison,
            )
            if any(value is None for value in required):
                raise ValueError("an available point comparison is incomplete")
            if len(self.source_record_ids) != 2:
                raise ValueError("an available point comparison requires two samples")
            if self.comparison_sample_count != 1:
                raise ValueError("an available point comparison requires one baseline")
        elif any(
            value is not None
            for value in (
                self.comparison_value,
                self.reference_minus_comparison,
                self.percent_change_from_comparison,
            )
        ):
            raise ValueError("an unavailable point comparison cannot expose a delta")
        return self


class RollingCoverage(BaseModel):
    result_version: Literal["1"] = RESULT_VERSION
    expected_day_count: Literal[28] = ROLLING_DAY_COUNT
    raw_sample_count: int = Field(ge=0)
    valid_sample_count: int = Field(ge=0, le=ROLLING_DAY_COUNT)
    coverage_fraction: float = Field(ge=0, le=1)
    minimum_coverage_fraction: Literal[0.95] = MINIMUM_COVERAGE_FRACTION
    missing_dates: list[date] = Field(default_factory=list)
    duplicate_starts: list[AwareDatetime] = Field(default_factory=list)
    ambiguous_dates: list[date] = Field(default_factory=list)
    is_sufficient: bool


class Rolling28ComparisonResult(BaseModel):
    result_version: Literal["1"] = RESULT_VERSION
    methodology_version: Literal["50hz.history.comparisons.v1"] = (
        HISTORY_METHODOLOGY_VERSION
    )
    status: ResultStatus
    reason: ResultReason
    reference_start: AwareDatetime
    reference_value: float | None = None
    median: float | None = None
    first_quartile: float | None = None
    third_quartile: float | None = None
    interquartile_range: float | None = None
    reference_minus_median: float | None = None
    reference_percentile: float | None = None
    source_record_ids: list[str] = Field(default_factory=list)
    coverage: RollingCoverage

    @model_validator(mode="after")
    def validate_availability(self) -> "Rolling28ComparisonResult":
        if self.status == ResultStatus.AVAILABLE:
            required = (
                self.reference_value,
                self.median,
                self.first_quartile,
                self.third_quartile,
                self.interquartile_range,
                self.reference_minus_median,
                self.reference_percentile,
            )
            if any(value is None for value in required):
                raise ValueError("an available rolling comparison is incomplete")
            if len(self.source_record_ids) != self.coverage.valid_sample_count + 1:
                raise ValueError("rolling sample provenance is incomplete")
        elif any(
            value is not None
            for value in (
                self.median,
                self.first_quartile,
                self.third_quartile,
                self.interquartile_range,
                self.reference_minus_median,
                self.reference_percentile,
            )
        ):
            raise ValueError("an unavailable rolling comparison cannot expose statistics")
        return self


class HistoryComparisonSet(BaseModel):
    result_version: Literal["1"] = RESULT_VERSION
    methodology_version: Literal["50hz.history.comparisons.v1"] = (
        HISTORY_METHODOLOGY_VERSION
    )
    methodology: HistoryMethodology = Field(default_factory=HistoryMethodology)
    identity: MetricSeriesIdentity
    reference_start: AwareDatetime
    compatibility_mismatches: list[str] = Field(default_factory=list)
    previous_period: PointComparisonResult
    same_time_yesterday: PointComparisonResult
    same_time_seven_days_ago: PointComparisonResult
    rolling_28_days: Rolling28ComparisonResult
