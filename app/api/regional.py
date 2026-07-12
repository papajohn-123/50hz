"""Compose the postcode view from cached facts and a bounded source fallback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.api.forecast_vintages import load_national_forecast_vintages
from app.api.models import RegionResponse, SourceReference
from app.charging import CarbonForecastPoint, find_cleanest_window
from app.persistence import ForecastRead, GridReadRepository, SourceMetadataRead
from app.regions import RegionalCarbonProvider, RegionalCarbonReading, RegionalDataUnavailableError
from app.sources.neso_carbon import normalize_outward_postcode
from app.sources.types import DataClassification


DEFAULT_CHARGING_WINDOW = timedelta(hours=1)
FORECAST_HORIZON = timedelta(hours=48)
MAX_REGIONAL_LAG = timedelta(minutes=90)


async def present_region(
    repository: GridReadRepository,
    provider: RegionalCarbonProvider,
    *,
    postcode: str,
    now: datetime,
    charging_duration: timedelta = DEFAULT_CHARGING_WINDOW,
) -> RegionResponse:
    instant = _aware_utc(now)
    normalized = normalize_outward_postcode(postcode)
    if charging_duration <= timedelta(0):
        raise ValueError("charging_duration must be positive")

    source_metadata = {source.id: source for source in await repository.list_sources()}
    regional = await _stored_regional_reading(
        repository,
        normalized,
        as_of=instant,
    )
    if regional is None:
        regional = await provider.fetch(normalized, as_of=instant)
    if regional.classification is not DataClassification.FORECAST:
        raise RegionalDataUnavailableError(
            "A current regional carbon forecast is unavailable"
        )

    forecast_start = _ceil_half_hour(instant)
    capture_cutoff = min(instant, regional.retrieved_at)
    vintages = await load_national_forecast_vintages(
        repository,
        window_start=min(regional.period_start, forecast_start),
        window_end=instant + FORECAST_HORIZON,
        captured_before=capture_cutoff,
        capture_lookback=timedelta(hours=24),
    )
    selection = None
    has_matching_period = False
    for vintage in vintages:
        if instant - vintage.captured_at > MAX_REGIONAL_LAG:
            continue
        national_period = vintage.matching_interval(
            regional.period_start,
            regional.period_end,
        )
        if national_period is None:
            continue
        has_matching_period = True
        future = [
            item
            for item in vintage.rows
            if item.valid_to is not None and item.valid_from >= forecast_start
        ]
        points = [
            CarbonForecastPoint(
                start=item.valid_from,
                end=item.valid_to,
                intensity_gco2_kwh=item.value,
                source_record_id=(
                    item.source_record_id
                    or f"{item.source_id}:{item.valid_from.isoformat()}"
                ),
            )
            for item in future
            if item.valid_to is not None
        ]
        cleanest = find_cleanest_window(points, duration=charging_duration)
        if cleanest is not None:
            selection = (vintage, national_period, cleanest)
            break

    if selection is None and not has_matching_period:
        raise RegionalDataUnavailableError(
            "A compatible national forecast for the regional half-hour is unavailable"
        )
    if selection is None:
        raise RegionalDataUnavailableError(
            "A contiguous national carbon forecast is unavailable for the "
            "requested charging duration"
        )
    national_vintage, national_period, cleanest = selection

    return RegionResponse(
        name=regional.name,
        postcode=normalized,
        carbon_intensity=regional.intensity_gco2_kwh,
        national_carbon_intensity=national_period.value,
        rating=regional.rating,
        regional_period_end=regional.period_end,
        regional_is_delayed=regional.period_end <= instant,
        cleanest_window_start=cleanest.start,
        cleanest_window_end=cleanest.end,
        charging_window_start=cleanest.start,
        charging_window_end=cleanest.end,
        forecast_issued_at=national_vintage.captured_at,
        forecast_captured_at=national_vintage.captured_at,
        source=_source_reference(regional, source_metadata.get(regional.source_id)),
    )


async def _stored_regional_reading(
    repository: GridReadRepository,
    postcode: str,
    *,
    as_of: datetime,
) -> RegionalCarbonReading | None:
    # London is continuously collected as region 13. Other outward postcodes
    # may exist when an earlier on-demand result has been persisted by a future
    # cache layer. Regional current values are forecast facts, not actuals.
    series_keys = (postcode, "region-13") if postcode == "SW1A" else (postcode,)
    for series_key in series_keys:
        forecasts = await repository.get_carbon_forecast(
            region_code=series_key,
            window_start=as_of - timedelta(minutes=30),
            window_end=as_of + timedelta(minutes=30),
            issued_before=as_of,
        )
        current = _covering_or_recent_forecast(forecasts, as_of=as_of)
        if current is not None:
            fallback_name = "London" if series_key == "region-13" else postcode
            return RegionalCarbonReading(
                postcode=postcode,
                name=str(current.attributes.get("regionName") or fallback_name),
                intensity_gco2_kwh=current.value,
                rating=str(current.attributes.get("index") or _rating(current.value)),
                period_start=current.valid_from,
                period_end=current.valid_to or current.valid_from + timedelta(minutes=30),
                retrieved_at=current.retrieved_at,
                source_id=current.source_id,
                dataset="carbon_intensity_regional",
                classification=DataClassification.FORECAST,
            )
    return None


def _covering_forecast(
    forecasts: tuple[ForecastRead, ...],
    *,
    as_of: datetime,
) -> ForecastRead | None:
    covering = [
        item
        for item in forecasts
        if item.valid_from <= as_of
        and (item.valid_to is None or as_of < item.valid_to)
    ]
    return max(covering, key=lambda item: (item.issued_at, item.retrieved_at), default=None)


def _covering_or_recent_forecast(
    forecasts: tuple[ForecastRead, ...],
    *,
    as_of: datetime,
) -> ForecastRead | None:
    covering = _covering_forecast(forecasts, as_of=as_of)
    if covering is not None:
        return covering
    recent = [
        item
        for item in forecasts
        if item.valid_to is not None
        and item.valid_to <= as_of
        and as_of - item.valid_to <= MAX_REGIONAL_LAG
    ]
    return max(
        recent,
        key=lambda item: (item.valid_to, item.issued_at, item.retrieved_at),
        default=None,
    )


def _source_reference(
    regional: RegionalCarbonReading,
    metadata: SourceMetadataRead | None,
) -> SourceReference:
    return SourceReference(
        id=regional.source_id,
        name=metadata.display_name if metadata else "NESO Carbon Intensity",
        dataset=metadata.dataset if metadata else regional.dataset,
        observed_at=regional.period_start,
        retrieved_at=regional.retrieved_at,
        cadence_seconds=metadata.expected_cadence_seconds if metadata else 1_800,
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


def _ceil_half_hour(value: datetime) -> datetime:
    value = _aware_utc(value)
    floor = value.replace(minute=30 if value.minute >= 30 else 0, second=0, microsecond=0)
    return floor if floor == value else floor + timedelta(minutes=30)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)
