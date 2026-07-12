from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from math import isclose, isfinite
from typing import Iterable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.history.models import (
    HalfHourObservation,
    MetricSeries,
    MetricSeriesIdentity,
)


MATERIALIZATION_METHODOLOGY_VERSION = "50hz.history.half-hour-mean.v1"
HALF_HOUR = timedelta(minutes=30)


class RawMetricObservation(BaseModel):
    """One source value at an exact timestamp and source revision."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    timestamp: AwareDatetime
    value: float = Field(allow_inf_nan=False)
    revision: int = Field(ge=0, strict=True)
    source_record_id: str = Field(min_length=1)
    retrieved_at: AwareDatetime | None = None


class RawMetricSeries(BaseModel):
    """A single identity-compatible source series at a fixed cadence."""

    model_config = ConfigDict(frozen=True)

    identity: MetricSeriesIdentity
    source_cadence_minutes: int = Field(ge=1, le=30, strict=True)
    observations: tuple[RawMetricObservation, ...] = ()

    @model_validator(mode="after")
    def validate_cadence(self) -> "RawMetricSeries":
        if 30 % self.source_cadence_minutes:
            raise ValueError("source cadence must divide exactly into 30 minutes")
        return self


class MaterializationStatus(StrEnum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"


class MaterializationReason(StrEnum):
    AVAILABLE = "available"
    MISSING_EXPECTED_TIMESTAMPS = "missing_expected_timestamps"
    UNEXPECTED_TIMESTAMPS = "unexpected_timestamps"
    COVERAGE_BELOW_THRESHOLD = "coverage_below_threshold"
    PARTIAL_COVERAGE_ACCEPTED = "partial_coverage_accepted"


class MaterializationMethodology(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: Literal["50hz.history.half-hour-mean.v1"] = (
        MATERIALIZATION_METHODOLOGY_VERSION
    )
    output_interval_minutes: Literal[30] = 30
    aggregate: Literal["mean"] = "mean"
    interpolation: Literal["none"] = "none"
    revision_rule: str = (
        "For each exact timestamp, select only the highest source revision."
    )
    duplicate_rule: str = (
        "Equivalent records at the selected revision are one logical sample; "
        "conflicting values at that revision are rejected."
    )
    timestamp_rule: str = (
        "Only exact cadence timestamps are aggregated; values are never shifted, "
        "forward-filled, or interpolated."
    )


class SelectedRawObservation(BaseModel):
    """The one logical value selected for an exact timestamp."""

    model_config = ConfigDict(frozen=True)

    timestamp: AwareDatetime
    value: float = Field(allow_inf_nan=False)
    revision: int = Field(ge=0)
    source_record_ids: tuple[str, ...] = Field(min_length=1)


class HalfHourMaterialization(BaseModel):
    """Auditable materialization evidence for one UTC half-hour."""

    model_config = ConfigDict(frozen=True)

    start: AwareDatetime
    end: AwareDatetime
    status: MaterializationStatus
    reasons: tuple[MaterializationReason, ...] = Field(min_length=1)
    expected_sample_count: int = Field(ge=1)
    selected_sample_count: int = Field(ge=0)
    raw_sample_count: int = Field(ge=0)
    coverage_fraction: float = Field(ge=0, le=1)
    missing_timestamps: tuple[AwareDatetime, ...] = ()
    unexpected_timestamps: tuple[AwareDatetime, ...] = ()
    selected_samples: tuple[SelectedRawObservation, ...] = ()
    unexpected_samples: tuple[SelectedRawObservation, ...] = ()
    raw_source_record_ids: tuple[str, ...] = ()
    value: float | None = Field(default=None, allow_inf_nan=False)
    materialized_source_record_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_evidence(self) -> "HalfHourMaterialization":
        start = self.start.astimezone(UTC)
        end = self.end.astimezone(UTC)
        if end - start != HALF_HOUR:
            raise ValueError("materialization intervals must be exactly 30 minutes")
        if not _is_half_hour_boundary(start):
            raise ValueError("materialization intervals must start on a UTC half-hour")
        if self.selected_sample_count != len(self.selected_samples):
            raise ValueError("selected sample count does not match its evidence")
        if self.raw_sample_count < self.selected_sample_count:
            raise ValueError("raw sample count cannot be below selected sample count")
        expected_fraction = self.selected_sample_count / self.expected_sample_count
        if not isclose(self.coverage_fraction, expected_fraction):
            raise ValueError("coverage fraction does not match selected samples")
        if tuple(sample.timestamp for sample in self.unexpected_samples) != (
            self.unexpected_timestamps
        ):
            raise ValueError("unexpected timestamp evidence is inconsistent")
        if self.status == MaterializationStatus.AVAILABLE:
            if self.value is None or self.materialized_source_record_id is None:
                raise ValueError("available intervals require a value and provenance")
        elif self.value is not None or self.materialized_source_record_id is not None:
            raise ValueError("insufficient intervals cannot expose a value")
        return self


class HalfHourMaterializationResult(BaseModel):
    """A compatible MetricSeries plus the evidence used to create it."""

    model_config = ConfigDict(frozen=True)

    methodology: MaterializationMethodology = Field(
        default_factory=MaterializationMethodology
    )
    identity: MetricSeriesIdentity
    start: AwareDatetime
    end: AwareDatetime
    source_cadence_minutes: int = Field(ge=1, le=30)
    minimum_coverage_fraction: float = Field(gt=0, le=1)
    interval_count: int = Field(ge=1)
    raw_sample_count: int = Field(ge=0)
    selected_sample_count: int = Field(ge=0)
    outside_bounds_raw_count: int = Field(ge=0)
    outside_bounds_timestamps: tuple[AwareDatetime, ...] = ()
    outside_bounds_source_record_ids: tuple[str, ...] = ()
    intervals: tuple[HalfHourMaterialization, ...] = Field(min_length=1)
    series: MetricSeries

    @model_validator(mode="after")
    def validate_result(self) -> "HalfHourMaterializationResult":
        if self.series.identity != self.identity:
            raise ValueError("materialization must preserve the source series identity")
        if self.interval_count != len(self.intervals):
            raise ValueError("interval count does not match interval evidence")
        if self.raw_sample_count != (
            sum(interval.raw_sample_count for interval in self.intervals)
            + self.outside_bounds_raw_count
        ):
            raise ValueError("raw sample count does not match interval evidence")
        if self.selected_sample_count != sum(
            interval.selected_sample_count for interval in self.intervals
        ):
            raise ValueError("selected sample count does not match interval evidence")

        available = [
            interval
            for interval in self.intervals
            if interval.status == MaterializationStatus.AVAILABLE
        ]
        if len(self.series.observations) != len(available):
            raise ValueError("series observations do not match available intervals")
        for interval, observation in zip(available, self.series.observations):
            if (
                observation.start != interval.start
                or observation.end != interval.end
                or observation.value != interval.value
                or observation.source_record_id
                != interval.materialized_source_record_id
            ):
                raise ValueError("series observation does not match interval evidence")
        return self


def materialize_half_hours(
    raw_series: RawMetricSeries,
    *,
    start: datetime,
    end: datetime,
    minimum_coverage_fraction: float = 1.0,
) -> HalfHourMaterializationResult:
    """Materialize exact-cadence raw values into UTC half-hour means.

    The default requires every expected source timestamp. A lower explicit
    threshold may accept a partial mean, but no unexpected timestamp is ever
    used and an insufficient interval never contributes an observation.
    """

    window_start = _aware_half_hour(start, "start")
    window_end = _aware_half_hour(end, "end")
    if window_end <= window_start:
        raise ValueError("end must be after start")
    threshold = _coverage_threshold(minimum_coverage_fraction)
    cadence = timedelta(minutes=raw_series.source_cadence_minutes)

    raw_by_interval: dict[datetime, list[RawMetricObservation]] = defaultdict(list)
    in_window: list[RawMetricObservation] = []
    outside: list[RawMetricObservation] = []
    for raw in raw_series.observations:
        timestamp = raw.timestamp.astimezone(UTC)
        if window_start <= timestamp < window_end:
            in_window.append(raw)
            index = int((timestamp - window_start) // HALF_HOUR)
            bucket_start = window_start + index * HALF_HOUR
            raw_by_interval[bucket_start].append(raw)
        else:
            outside.append(raw)
    selected_by_timestamp = _select_highest_revisions(in_window)
    selected_by_interval: dict[datetime, list[SelectedRawObservation]] = defaultdict(list)
    for timestamp, selected in selected_by_timestamp.items():
        index = int((timestamp - window_start) // HALF_HOUR)
        bucket_start = window_start + index * HALF_HOUR
        selected_by_interval[bucket_start].append(selected)

    interval_results: list[HalfHourMaterialization] = []
    observations: list[HalfHourObservation] = []
    interval_start = window_start
    while interval_start < window_end:
        interval_end = interval_start + HALF_HOUR
        expected_timestamps = tuple(
            interval_start + index * cadence
            for index in range(30 // raw_series.source_cadence_minutes)
        )
        expected_set = set(expected_timestamps)
        selected_samples = tuple(
            selected_by_timestamp[timestamp]
            for timestamp in expected_timestamps
            if timestamp in selected_by_timestamp
        )
        missing = tuple(
            timestamp
            for timestamp in expected_timestamps
            if timestamp not in selected_by_timestamp
        )
        unexpected_samples = tuple(
            sample
            for sample in sorted(
                selected_by_interval.get(interval_start, ()),
                key=lambda candidate: candidate.timestamp,
            )
            if sample.timestamp not in expected_set
        )
        unexpected_timestamps = tuple(
            sample.timestamp for sample in unexpected_samples
        )
        raw_interval = raw_by_interval.get(interval_start, [])
        selected_count = len(selected_samples)
        expected_count = len(expected_timestamps)
        coverage_fraction = selected_count / expected_count
        sufficient = (
            coverage_fraction >= threshold and not unexpected_samples
        )

        reasons: list[MaterializationReason] = []
        if missing:
            reasons.append(MaterializationReason.MISSING_EXPECTED_TIMESTAMPS)
        if unexpected_samples:
            reasons.append(MaterializationReason.UNEXPECTED_TIMESTAMPS)
        if coverage_fraction < threshold:
            reasons.append(MaterializationReason.COVERAGE_BELOW_THRESHOLD)
        if sufficient and missing:
            reasons.append(MaterializationReason.PARTIAL_COVERAGE_ACCEPTED)
        if not reasons:
            reasons.append(MaterializationReason.AVAILABLE)

        value: float | None = None
        materialized_id: str | None = None
        if sufficient:
            value = _finite_mean(sample.value for sample in selected_samples)
            materialized_id = _materialized_record_id(
                identity=raw_series.identity,
                interval_start=interval_start,
                selected=selected_samples,
            )

        result = HalfHourMaterialization(
            start=interval_start,
            end=interval_end,
            status=(
                MaterializationStatus.AVAILABLE
                if sufficient
                else MaterializationStatus.INSUFFICIENT_DATA
            ),
            reasons=tuple(reasons),
            expected_sample_count=expected_count,
            selected_sample_count=selected_count,
            raw_sample_count=len(raw_interval),
            coverage_fraction=coverage_fraction,
            missing_timestamps=missing,
            unexpected_timestamps=unexpected_timestamps,
            selected_samples=selected_samples,
            unexpected_samples=unexpected_samples,
            raw_source_record_ids=tuple(
                sorted({sample.source_record_id for sample in raw_interval})
            ),
            value=value,
            materialized_source_record_id=materialized_id,
        )
        interval_results.append(result)
        if sufficient:
            observations.append(
                HalfHourObservation(
                    start=interval_start,
                    end=interval_end,
                    value=value,
                    source_record_id=materialized_id,
                )
            )
        interval_start = interval_end

    outside_timestamps = tuple(
        sorted({sample.timestamp.astimezone(UTC) for sample in outside})
    )
    return HalfHourMaterializationResult(
        identity=raw_series.identity,
        start=window_start,
        end=window_end,
        source_cadence_minutes=raw_series.source_cadence_minutes,
        minimum_coverage_fraction=threshold,
        interval_count=len(interval_results),
        raw_sample_count=len(raw_series.observations),
        selected_sample_count=sum(
            interval.selected_sample_count for interval in interval_results
        ),
        outside_bounds_raw_count=len(outside),
        outside_bounds_timestamps=outside_timestamps,
        outside_bounds_source_record_ids=tuple(
            sorted({sample.source_record_id for sample in outside})
        ),
        intervals=tuple(interval_results),
        series=MetricSeries(
            identity=raw_series.identity,
            observations=observations,
        ),
    )


def _select_highest_revisions(
    observations: Iterable[RawMetricObservation],
) -> dict[datetime, SelectedRawObservation]:
    by_timestamp: dict[datetime, list[RawMetricObservation]] = defaultdict(list)
    for observation in observations:
        by_timestamp[observation.timestamp.astimezone(UTC)].append(observation)

    selected: dict[datetime, SelectedRawObservation] = {}
    for timestamp, candidates in by_timestamp.items():
        highest_revision = max(candidate.revision for candidate in candidates)
        highest = [
            candidate
            for candidate in candidates
            if candidate.revision == highest_revision
        ]
        values = {candidate.value for candidate in highest}
        if len(values) != 1:
            raise ValueError(
                "conflicting values at highest revision "
                f"{highest_revision} for timestamp {timestamp.isoformat()}"
            )
        selected[timestamp] = SelectedRawObservation(
            timestamp=timestamp,
            value=highest[0].value,
            revision=highest_revision,
            source_record_ids=tuple(
                sorted({candidate.source_record_id for candidate in highest})
            ),
        )
    return selected


def _aware_half_hour(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    utc = value.astimezone(UTC)
    if not _is_half_hour_boundary(utc):
        raise ValueError(f"{field} must be on an exact UTC half-hour boundary")
    return utc


def _is_half_hour_boundary(value: datetime) -> bool:
    return value.minute in (0, 30) and value.second == 0 and value.microsecond == 0


def _coverage_threshold(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("minimum_coverage_fraction must be a number")
    threshold = float(value)
    if not isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("minimum_coverage_fraction must be finite and in (0, 1]")
    return threshold


def _finite_mean(values: Iterable[float]) -> float:
    materialized = tuple(values)
    if not materialized:
        raise ValueError("cannot materialize an interval without selected samples")
    mean = sum(value / len(materialized) for value in materialized)
    if not isfinite(mean):
        raise ValueError("materialized mean must be finite")
    return mean


def _materialized_record_id(
    *,
    identity: MetricSeriesIdentity,
    interval_start: datetime,
    selected: tuple[SelectedRawObservation, ...],
) -> str:
    evidence = [
        MATERIALIZATION_METHODOLOGY_VERSION,
        identity.identity_version,
        identity.metric_id,
        identity.geography,
        identity.unit,
        identity.fact_class,
        identity.source_id,
        identity.methodology_version,
        interval_start.astimezone(UTC).isoformat(),
    ]
    for sample in selected:
        evidence.extend(
            (
                sample.timestamp.astimezone(UTC).isoformat(),
                str(sample.revision),
                repr(sample.value),
                *sample.source_record_ids,
            )
        )
    digest = sha256("\x1f".join(evidence).encode("utf-8")).hexdigest()
    return f"50hz:half-hour-mean:v1:{digest}"
