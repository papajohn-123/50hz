"""Deterministic, boundary-reviewed forecast verification.

Only stored source vintages are considered. Each distinct source issue/capture
is retained in its horizon bucket, with only its latest immutable correction
selected; no vintage, timestamp, or outturn is synthesized.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.forecast_contract import (
    CAPTURE_TIME_ISSUE_BASIS,
    NATIONAL_FORECAST_METHODOLOGY_VERSION,
    SOURCE_ISSUE_TIME_UNAVAILABLE,
    SOURCE_PUBLISHED_TIME_BASIS,
)


VERIFICATION_REGISTRY_VERSION = "2026-07-12.forecast-verification.1"
VERIFICATION_METHODOLOGY_VERSION = "50hz.forecast-verification.exact-vintage.v1"
MINIMUM_VERIFIED_SAMPLES = 100
MINIMUM_COVERAGE_FRACTION = 0.90
SAFE_WAPE_DENOMINATOR = 1e-9


class VerificationMetric(StrEnum):
    NATIONAL_DEMAND = "national_demand"
    WIND_GENERATION = "wind_generation"
    NATIONAL_CARBON_INTENSITY = "national_carbon_intensity"


class HorizonBucket(StrEnum):
    ZERO_TO_THREE_HOURS = "0_3h"
    THREE_TO_TWELVE_HOURS = "3_12h"
    TWELVE_TO_TWENTY_FOUR_HOURS = "12_24h"
    TWENTY_FOUR_TO_FORTY_EIGHT_HOURS = "24_48h"

    @property
    def minimum_hours(self) -> int:
        return {
            self.ZERO_TO_THREE_HOURS: 0,
            self.THREE_TO_TWELVE_HOURS: 3,
            self.TWELVE_TO_TWENTY_FOUR_HOURS: 12,
            self.TWENTY_FOUR_TO_FORTY_EIGHT_HOURS: 24,
        }[self]

    @property
    def maximum_hours(self) -> int:
        return {
            self.ZERO_TO_THREE_HOURS: 3,
            self.THREE_TO_TWELVE_HOURS: 12,
            self.TWELVE_TO_TWENTY_FOUR_HOURS: 24,
            self.TWENTY_FOUR_TO_FORTY_EIGHT_HOURS: 48,
        }[self]

    def contains(self, horizon: timedelta) -> bool:
        seconds = horizon.total_seconds()
        lower = self.minimum_hours * 3_600
        upper = self.maximum_hours * 3_600
        # The outer 48-hour boundary is accepted; shared interior boundaries
        # belong to the later bucket, so no vintage can count twice.
        return lower <= seconds < upper or (
            self is self.TWENTY_FOUR_TO_FORTY_EIGHT_HOURS and seconds == upper
        )


HORIZON_BUCKETS = tuple(HorizonBucket)


@dataclass(frozen=True, slots=True)
class VerificationTarget:
    metric: VerificationMetric
    display_name: str
    unit: str
    forecast_source_id: str
    forecast_dataset: str
    outturn_source_id: str
    outturn_dataset: str
    forecast_methodology_version: str
    outturn_methodology_version: str
    issue_time_basis: str
    effective_vintage_time_basis: str
    expected_interval_minutes: int
    forecast_ingestion_lock: str
    outturn_ingestion_lock: str

    def __post_init__(self) -> None:
        if self.issue_time_basis not in {
            SOURCE_PUBLISHED_TIME_BASIS,
            SOURCE_ISSUE_TIME_UNAVAILABLE,
        }:
            raise ValueError("issue basis must be reviewed")
        if self.effective_vintage_time_basis not in {
            SOURCE_PUBLISHED_TIME_BASIS,
            CAPTURE_TIME_ISSUE_BASIS,
        }:
            raise ValueError("effective vintage basis must be reviewed")
        if (
            self.issue_time_basis == SOURCE_ISSUE_TIME_UNAVAILABLE
        ) != (
            self.effective_vintage_time_basis == CAPTURE_TIME_ISSUE_BASIS
        ):
            raise ValueError(
                "an unavailable source issue time must use retrieval as its vintage"
            )
        if self.expected_interval_minutes not in {30, 60}:
            raise ValueError("verification cadence must be explicitly reviewed")


VERIFICATION_TARGETS = (
    VerificationTarget(
        metric=VerificationMetric.NATIONAL_DEMAND,
        display_name="National demand forecast",
        unit="MW",
        forecast_source_id="elexon.ndf",
        forecast_dataset="NDF",
        outturn_source_id="elexon.indo",
        outturn_dataset="INDO",
        forecast_methodology_version="elexon-ndf-national-v1",
        outturn_methodology_version="indo-national-demand-v1",
        issue_time_basis=SOURCE_PUBLISHED_TIME_BASIS,
        effective_vintage_time_basis=SOURCE_PUBLISHED_TIME_BASIS,
        expected_interval_minutes=30,
        forecast_ingestion_lock="elexon.ndf",
        outturn_ingestion_lock="elexon.indo",
    ),
    VerificationTarget(
        metric=VerificationMetric.WIND_GENERATION,
        display_name="Wind generation forecast",
        unit="MW",
        forecast_source_id="elexon.windfor",
        forecast_dataset="WINDFOR",
        outturn_source_id="elexon.fuelinst",
        outturn_dataset="FUELINST wind",
        forecast_methodology_version="elexon-windfor-fuelinst-boundary-v1",
        outturn_methodology_version="fuelinst-generation-v1",
        issue_time_basis=SOURCE_PUBLISHED_TIME_BASIS,
        effective_vintage_time_basis=SOURCE_PUBLISHED_TIME_BASIS,
        expected_interval_minutes=60,
        forecast_ingestion_lock="elexon.windfor",
        outturn_ingestion_lock="elexon.fuelinst",
    ),
    VerificationTarget(
        metric=VerificationMetric.NATIONAL_CARBON_INTENSITY,
        display_name="National carbon-intensity forecast",
        unit="gCO2/kWh",
        forecast_source_id="neso.carbon-intensity-national",
        forecast_dataset="NESO national carbon forecast",
        outturn_source_id="neso.carbon-intensity-national",
        outturn_dataset="NESO national carbon estimate",
        forecast_methodology_version=NATIONAL_FORECAST_METHODOLOGY_VERSION,
        outturn_methodology_version="neso-national-carbon-v1",
        issue_time_basis=SOURCE_ISSUE_TIME_UNAVAILABLE,
        effective_vintage_time_basis=CAPTURE_TIME_ISSUE_BASIS,
        expected_interval_minutes=30,
        forecast_ingestion_lock="neso.carbon.national.forecast",
        outturn_ingestion_lock="neso.carbon.national.current",
    ),
)
TARGET_BY_METRIC = {target.metric: target for target in VERIFICATION_TARGETS}


@dataclass(frozen=True, slots=True)
class ForecastEvidence:
    observation_id: UUID
    valid_from: datetime
    issued_at: datetime
    captured_at: datetime
    value: float
    revision: int

    def __post_init__(self) -> None:
        _aware(self.valid_from, "valid_from")
        _aware(self.issued_at, "issued_at")
        _aware(self.captured_at, "captured_at")
        if not math.isfinite(self.value):
            raise ValueError("forecast values must be finite")
        if self.revision < 0:
            raise ValueError("forecast revision must be non-negative")


@dataclass(frozen=True, slots=True)
class OutturnEvidence:
    observation_id: UUID
    observed_at: datetime
    retrieved_at: datetime
    value: float
    revision: int

    def __post_init__(self) -> None:
        _aware(self.observed_at, "observed_at")
        _aware(self.retrieved_at, "retrieved_at")
        if not math.isfinite(self.value):
            raise ValueError("outturn values must be finite")
        if self.revision < 0:
            raise ValueError("outturn revision must be non-negative")


@dataclass(frozen=True, slots=True)
class VerificationPair:
    target: VerificationTarget
    horizon: HorizonBucket
    forecast: ForecastEvidence
    outturn: OutturnEvidence
    signed_error: float
    absolute_error: float
    content_sha256: str

    @property
    def forecast_vintage_at(self) -> datetime:
        return _effective_vintage_at(
            self.forecast,
            basis=self.target.effective_vintage_time_basis,
        )

    @property
    def forecast_source_issued_at(self) -> datetime | None:
        if self.target.issue_time_basis == SOURCE_ISSUE_TIME_UNAVAILABLE:
            return None
        return self.forecast.issued_at.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    target: VerificationTarget
    horizon: HorizonBucket
    window_start: datetime
    window_end: datetime
    status: str
    reason: str
    mae: float | None
    bias: float | None
    wape_percent: float | None
    verified_sample_count: int
    expected_sample_count: int
    coverage_fraction: float
    evidence_checksum: str
    source_watermark_at: datetime | None

    @property
    def display_eligible(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True, slots=True)
class VerificationBundle:
    target: VerificationTarget
    pairs: tuple[VerificationPair, ...]
    results: tuple[VerificationResult, ...]

    @property
    def result_checksum(self) -> str:
        return _checksum(
            {
                "registryVersion": VERIFICATION_REGISTRY_VERSION,
                "methodologyVersion": VERIFICATION_METHODOLOGY_VERSION,
                "metric": self.target.metric.value,
                "results": [result.evidence_checksum for result in self.results],
            }
        )


def verify_forecasts(
    target: VerificationTarget,
    *,
    forecasts: tuple[ForecastEvidence, ...],
    outturns: tuple[OutturnEvidence, ...],
    window_start: datetime,
    window_end: datetime,
) -> VerificationBundle:
    """Pair exact stored vintages to exact reviewed normalized outturns."""

    start = _aware(window_start, "window_start")
    end = _aware(window_end, "window_end")
    if end <= start:
        raise ValueError("verification window must be positive")

    selected_forecasts = _select_vintages(
        forecasts,
        window_start=start,
        window_end=end,
        effective_vintage_time_basis=target.effective_vintage_time_basis,
    )
    selected_outturns = _select_outturn_revisions(
        outturns,
        window_start=start,
        window_end=end,
    )

    pairs: list[VerificationPair] = []
    results: list[VerificationResult] = []
    for horizon in HORIZON_BUCKETS:
        selected = tuple(
            forecast
            for bucket, forecast in selected_forecasts
            if bucket is horizon
        )
        bucket_pairs: list[VerificationPair] = []
        for forecast in selected:
            valid_from = forecast.valid_from.astimezone(UTC)
            outturn = selected_outturns.get(valid_from)
            if outturn is None:
                continue
            signed_error = forecast.value - outturn.value
            pair_payload = {
                "registryVersion": VERIFICATION_REGISTRY_VERSION,
                "methodologyVersion": VERIFICATION_METHODOLOGY_VERSION,
                "metric": target.metric.value,
                "horizon": horizon.value,
                "validFrom": valid_from.isoformat(),
                "forecastObservationID": str(forecast.observation_id),
                "forecastRevision": forecast.revision,
                "forecastVintageAt": _effective_vintage_at(
                    forecast,
                    basis=target.effective_vintage_time_basis,
                ).isoformat(),
                "forecastSourceIssuedAt": (
                    None
                    if target.issue_time_basis == SOURCE_ISSUE_TIME_UNAVAILABLE
                    else forecast.issued_at.astimezone(UTC).isoformat()
                ),
                "forecastCapturedAt": forecast.captured_at.astimezone(UTC).isoformat(),
                "forecastValue": forecast.value,
                "outturnObservationID": str(outturn.observation_id),
                "outturnRevision": outturn.revision,
                "outturnValue": outturn.value,
            }
            bucket_pairs.append(
                VerificationPair(
                    target=target,
                    horizon=horizon,
                    forecast=forecast,
                    outturn=outturn,
                    signed_error=signed_error,
                    absolute_error=abs(signed_error),
                    content_sha256=_checksum(pair_payload),
                )
            )
        pairs.extend(bucket_pairs)
        results.append(
            _aggregate(
                target,
                horizon=horizon,
                selected=selected,
                pairs=tuple(bucket_pairs),
                window_start=start,
                window_end=end,
            )
        )
    return VerificationBundle(target=target, pairs=tuple(pairs), results=tuple(results))


def _select_vintages(
    forecasts: tuple[ForecastEvidence, ...],
    *,
    window_start: datetime,
    window_end: datetime,
    effective_vintage_time_basis: str,
) -> tuple[tuple[HorizonBucket, ForecastEvidence], ...]:
    # First select the latest immutable correction for a source vintage.
    latest_revision: dict[tuple[datetime, datetime], ForecastEvidence] = {}
    for forecast in forecasts:
        valid = forecast.valid_from.astimezone(UTC)
        captured = forecast.captured_at.astimezone(UTC)
        if not window_start <= valid < window_end:
            continue
        basis = _effective_vintage_at(
            forecast,
            basis=effective_vintage_time_basis,
        )
        # Retrieval time is an effective vintage boundary only when the source
        # publishes no issue time. It is never presented as a source issue.
        # Source-published NDF/WINDFOR vintages remain valid when imported later.
        if basis > valid:
            continue
        identity = (valid, basis)
        existing = latest_revision.get(identity)
        if existing is None or (
            forecast.revision,
            captured,
            str(forecast.observation_id),
        ) > (
            existing.revision,
            existing.captured_at.astimezone(UTC),
            str(existing.observation_id),
        ):
            latest_revision[identity] = forecast

    # Every distinct stored source vintage is evidence. The correction selector
    # above prevents one vintage revision from counting twice, while retaining
    # genuinely different source issues/captures in the same horizon bucket.
    selected: list[tuple[HorizonBucket, ForecastEvidence]] = []
    for forecast in latest_revision.values():
        valid = forecast.valid_from.astimezone(UTC)
        basis = _effective_vintage_at(
            forecast,
            basis=effective_vintage_time_basis,
        )
        horizon = next(
            (bucket for bucket in HORIZON_BUCKETS if bucket.contains(valid - basis)),
            None,
        )
        if horizon is None:
            continue
        selected.append((horizon, forecast))
    return tuple(
        sorted(
            selected,
            key=lambda item: (
                HORIZON_BUCKETS.index(item[0]),
                item[1].valid_from.astimezone(UTC),
                _effective_vintage_at(
                    item[1],
                    basis=effective_vintage_time_basis,
                ),
                item[1].revision,
                str(item[1].observation_id),
            ),
        )
    )


def _select_outturn_revisions(
    outturns: tuple[OutturnEvidence, ...],
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict[datetime, OutturnEvidence]:
    selected: dict[datetime, OutturnEvidence] = {}
    for outturn in outturns:
        observed = outturn.observed_at.astimezone(UTC)
        if not window_start <= observed < window_end:
            continue
        existing = selected.get(observed)
        if existing is None or (
            outturn.revision,
            outturn.retrieved_at.astimezone(UTC),
            str(outturn.observation_id),
        ) > (
            existing.revision,
            existing.retrieved_at.astimezone(UTC),
            str(existing.observation_id),
        ):
            selected[observed] = outturn
    return selected


def _aggregate(
    target: VerificationTarget,
    *,
    horizon: HorizonBucket,
    selected: tuple[ForecastEvidence, ...],
    pairs: tuple[VerificationPair, ...],
    window_start: datetime,
    window_end: datetime,
) -> VerificationResult:
    expected_count = len(selected)
    verified_count = len(pairs)
    coverage = verified_count / expected_count if expected_count else 0.0
    mae = bias = wape = None
    if pairs:
        mae = math.fsum(pair.absolute_error for pair in pairs) / verified_count
        bias = math.fsum(pair.signed_error for pair in pairs) / verified_count
        denominator = math.fsum(abs(pair.outturn.value) for pair in pairs)
        if denominator > SAFE_WAPE_DENOMINATOR:
            wape = (
                math.fsum(pair.absolute_error for pair in pairs)
                / denominator
                * 100.0
            )

    status, reason = _status_and_reason(
        expected=expected_count,
        verified=verified_count,
        coverage=coverage,
    )

    evidence_checksum = _checksum(
        {
            "registryVersion": VERIFICATION_REGISTRY_VERSION,
            "methodologyVersion": VERIFICATION_METHODOLOGY_VERSION,
            "metric": target.metric.value,
            "horizon": horizon.value,
            "windowStart": window_start.isoformat(),
            "windowEnd": window_end.isoformat(),
            "expectedVintages": [
                {
                    "validFrom": forecast.valid_from.astimezone(UTC).isoformat(),
                    "vintageAt": _effective_vintage_at(
                        forecast,
                        basis=target.effective_vintage_time_basis,
                    ).isoformat(),
                    "sourceIssuedAt": (
                        None
                        if target.issue_time_basis == SOURCE_ISSUE_TIME_UNAVAILABLE
                        else forecast.issued_at.astimezone(UTC).isoformat()
                    ),
                    "observationID": str(forecast.observation_id),
                    "revision": forecast.revision,
                    "value": forecast.value,
                }
                for forecast in selected
            ],
            "pairs": [pair.content_sha256 for pair in pairs],
        }
    )
    watermarks = [forecast.captured_at for forecast in selected]
    watermarks.extend(pair.outturn.retrieved_at for pair in pairs)
    return VerificationResult(
        target=target,
        horizon=horizon,
        window_start=window_start,
        window_end=window_end,
        status=status,
        reason=reason,
        mae=mae,
        bias=bias,
        wape_percent=wape,
        verified_sample_count=verified_count,
        expected_sample_count=expected_count,
        coverage_fraction=coverage,
        evidence_checksum=evidence_checksum,
        source_watermark_at=max(watermarks).astimezone(UTC) if watermarks else None,
    )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def _effective_vintage_at(
    forecast: ForecastEvidence,
    *,
    basis: str,
) -> datetime:
    if basis == CAPTURE_TIME_ISSUE_BASIS:
        return forecast.captured_at.astimezone(UTC)
    if basis == SOURCE_PUBLISHED_TIME_BASIS:
        return forecast.issued_at.astimezone(UTC)
    raise ValueError("unsupported effective vintage time basis")


def _status_and_reason(
    *,
    expected: int,
    verified: int,
    coverage: float,
) -> tuple[str, str]:
    eligible = (
        verified >= MINIMUM_VERIFIED_SAMPLES
        and coverage >= MINIMUM_COVERAGE_FRACTION
    )
    if eligible:
        return "available", "eligible"
    if expected == 0:
        return "insufficient_data", "no_forecasts"
    if verified == 0:
        return "insufficient_data", "no_compatible_outturns"
    if verified < MINIMUM_VERIFIED_SAMPLES and coverage < MINIMUM_COVERAGE_FRACTION:
        return "insufficient_data", "sample_and_coverage_thresholds_not_met"
    if verified < MINIMUM_VERIFIED_SAMPLES:
        return "insufficient_data", "fewer_than_100_verified_samples"
    return "insufficient_data", "coverage_below_90_percent"


def _checksum(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
