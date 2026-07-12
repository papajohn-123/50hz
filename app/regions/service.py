"""Bounded on-demand regional carbon lookups for uncached postcodes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.sources.client import AsyncJSONClient, RetryPolicy
from app.sources.neso_carbon import PostcodeCarbonIntensityAdapter, normalize_outward_postcode
from app.sources.types import CarbonIntensityRecord, DataClassification, ObservationWindow


MAX_REGIONAL_LAG = timedelta(minutes=90)


class RegionalDataUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RegionalCarbonReading:
    postcode: str
    name: str
    intensity_gco2_kwh: float
    rating: str
    period_start: datetime
    period_end: datetime
    retrieved_at: datetime
    source_id: str
    dataset: str
    classification: DataClassification


class RegionalCarbonProvider(Protocol):
    async def fetch(self, postcode: str, *, as_of: datetime) -> RegionalCarbonReading: ...


class OnDemandRegionalCarbonProvider:
    """Fetch one postcode with strict validation and a short, owned HTTP client."""

    def __init__(self, *, base_url: str, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout_seconds = timeout_seconds

    async def fetch(self, postcode: str, *, as_of: datetime) -> RegionalCarbonReading:
        normalized = normalize_outward_postcode(postcode)
        instant = _aware_utc(as_of)
        window = ObservationWindow(
            start=instant - timedelta(minutes=30),
            end=instant + timedelta(minutes=30),
        )
        async with AsyncJSONClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0.1,
                max_delay_seconds=0.5,
            ),
        ) as client:
            result = await PostcodeCarbonIntensityAdapter(client, normalized).fetch(window)

        record = _current_record(result.records, as_of=instant)
        if record is None:
            raise RegionalDataUnavailableError(
                "The regional source returned no carbon value for the current period"
            )
        return RegionalCarbonReading(
            postcode=normalized,
            name=record.region_name or normalized,
            intensity_gco2_kwh=float(record.intensity_g_co2_per_kwh),
            rating=record.index or _rating(record.intensity_g_co2_per_kwh),
            period_start=record.period_start,
            period_end=record.period_end,
            retrieved_at=record.retrieved_at,
            source_id=result.source_id,
            dataset=result.dataset,
            classification=record.classification,
        )


def _current_record(
    records: tuple[CarbonIntensityRecord, ...],
    *,
    as_of: datetime,
) -> CarbonIntensityRecord | None:
    current = [record for record in records if record.period_start <= as_of < record.period_end]
    candidates = current
    if not candidates:
        candidates = [
            record
            for record in records
            if record.period_end <= as_of
            and as_of - record.period_end <= MAX_REGIONAL_LAG
        ]
    if not candidates:
        return None
    # Regional "now" is a forecast contract. The upstream payload can contain
    # an estimated actual for the same half-hour; mixing that with the national
    # forecast would create a false like-for-like comparison.
    forecasts = [
        record
        for record in candidates
        if record.classification is DataClassification.FORECAST
    ]
    return max(
        forecasts,
        key=lambda record: (
            record.period_end,
            record.retrieved_at,
        ),
        default=None,
    )


def _rating(intensity: float) -> str:
    if intensity <= 50:
        return "very low"
    if intensity <= 100:
        return "low"
    if intensity <= 180:
        return "moderate"
    if intensity <= 250:
        return "high"
    return "very high"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(UTC)
