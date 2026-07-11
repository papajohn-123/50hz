from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta

from app.api.classification import build_headline
from app.api.models import (
    FactClass,
    FuelReading,
    GridEvent,
    GridMetric,
    GridSnapshotResponse,
    GridTimelineResponse,
    GridTimelineSample,
    InterconnectorFlow,
    MobileFreshness,
    SourceReference,
)
from app.persistence.reads import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    ForecastRead,
    FrequencyRead,
    GenerationRead,
    GridTimelineRead,
    InterconnectorRead,
    ReadProvenance,
    SourceMetadataRead,
)


class GridDataUnavailableError(RuntimeError):
    pass


COUNTRY_CODES = {
    "france": "FR",
    "ireland": "IE",
    "northern ireland": "GB-NIR",
    "netherlands": "NL",
    "belgium": "BE",
    "norway": "NO",
    "denmark": "DK",
}


FUEL_DISPLAY_ORDER = (
    "wind",
    "gas",
    "nuclear",
    "solar",
    "biomass",
    "hydro",
    "storage",
    "coal",
    "other",
)


def _mobile_fuel(fuel: str) -> str:
    if fuel == "pumped_storage":
        return "storage"
    if fuel in {"coal", "oil", "other", "unknown"}:
        return "other"
    return fuel


def _source_map(sources: Iterable[SourceMetadataRead]) -> dict[str, SourceMetadataRead]:
    return {source.id: source for source in sources}


def _source_reference(
    source: SourceMetadataRead,
    provenance: ReadProvenance,
) -> SourceReference:
    return SourceReference(
        id=source.id,
        name=source.display_name,
        dataset=source.dataset,
        observed_at=provenance.observed_at,
        retrieved_at=provenance.retrieved_at,
        cadence_seconds=source.expected_cadence_seconds,
    )


def aggregate_generation(readings: Iterable[GenerationRead]) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for reading in readings:
        result[_mobile_fuel(reading.fuel_type)] += max(0.0, reading.megawatts)
    return dict(result)


def _fuel_readings(
    generation_mw: Mapping[str, float],
    *,
    fact_class: FactClass,
    changes: Mapping[str, float] | None = None,
) -> list[FuelReading]:
    total = sum(max(0.0, value) for value in generation_mw.values())
    ordered = sorted(generation_mw.items(), key=lambda item: (-item[1], item[0]))
    changes = changes or {}
    return [
        FuelReading(
            fuel=fuel,
            megawatts=round(megawatts, 1),
            share=round(megawatts / total, 6) if total else 0,
            change_one_hour=round(changes.get(fuel, 0.0), 1),
            rank=index + 1,
            fact_class=fact_class,
        )
        for index, (fuel, megawatts) in enumerate(ordered)
    ]


def _source_references_for_current(read: CurrentGridRead) -> list[SourceReference]:
    metadata = _source_map(read.sources)
    latest: dict[str, ReadProvenance] = {}
    readings = [
        *read.generation,
        *read.interconnectors,
        *(value for value in (read.demand, read.frequency, read.carbon) if value is not None),
    ]
    for reading in readings:
        source_id = reading.provenance.source_id
        current = latest.get(source_id)
        if current is None or reading.provenance.observed_at > current.observed_at:
            latest[source_id] = reading.provenance
    return [
        _source_reference(metadata[source_id], provenance)
        for source_id, provenance in sorted(latest.items())
        if source_id in metadata
    ]


def present_current(
    read: CurrentGridRead,
    *,
    active_event: GridEvent | None = None,
    previous_generation_mw: Mapping[str, float] | None = None,
) -> GridSnapshotResponse:
    if not read.generation or read.demand is None or read.carbon is None:
        missing = []
        if not read.generation:
            missing.append("generation")
        if read.demand is None:
            missing.append("demand")
        if read.carbon is None:
            missing.append("carbon")
        raise GridDataUnavailableError("Missing required grid data: " + ", ".join(missing))

    effective_at = read.effective_at
    retrieved_at = read.retrieved_at
    if effective_at is None or retrieved_at is None:
        raise GridDataUnavailableError("Grid observations have no usable timestamps")
    generation_mw = aggregate_generation(read.generation)
    net_import_mw = sum(flow.megawatts for flow in read.interconnectors)
    if net_import_mw > 0:
        generation_mw["imports"] = net_import_mw
    previous = previous_generation_mw or {}
    changes = {
        fuel: value - previous.get(fuel, value)
        for fuel, value in generation_mw.items()
    }
    frequency_hz = read.frequency.hertz if read.frequency else None

    required_readings = [
        *read.generation,
        *read.interconnectors,
        read.demand,
        read.carbon,
    ]
    if read.frequency:
        required_readings.append(read.frequency)
    required_times = [item.provenance.observed_at for item in required_readings]
    age_seconds = max(
        0,
        int((read.requested_at - min(required_times)).total_seconds()),
    )
    source_metadata = _source_map(read.sources)
    has_stale_required_fact = any(
        _reading_is_stale(
            observed_at=item.provenance.observed_at,
            source=source_metadata.get(item.provenance.source_id),
            requested_at=read.requested_at,
        )
        for item in required_readings
    )
    # Reported plant unavailability and general SYSWARN publications remain
    # visible events, but they do not by themselves mean the live national
    # system is in a critical state. Only an explicitly classified critical
    # incident may promote the whole instrument to its red state.
    if active_event and active_event.severity == "critical":
        freshness = MobileFreshness.CRITICAL
    elif has_stale_required_fact:
        freshness = MobileFreshness.STALE
    else:
        freshness = MobileFreshness.LIVE

    sources = _source_references_for_current(read)
    source_ids = {source.id for source in sources}
    demand_source = read.demand.provenance.source_id
    carbon_source = read.carbon.provenance.source_id
    frequency_source = read.frequency.provenance.source_id if read.frequency else None
    if demand_source not in source_ids or carbon_source not in source_ids:
        raise GridDataUnavailableError("Required source metadata is missing")

    return GridSnapshotResponse(
        timestamp=effective_at,
        retrieved_at=retrieved_at,
        freshness=freshness,
        freshness_age_seconds=age_seconds,
        headline=build_headline(
            carbon_intensity=read.carbon.intensity_gco2_kwh,
            frequency_hz=frequency_hz,
            net_import_mw=net_import_mw,
            generation_mw=generation_mw,
            demand_mw=read.demand.megawatts,
            active_system_warning=freshness is MobileFreshness.CRITICAL,
        ),
        frequency=(
            GridMetric(value=frequency_hz, unit="Hz", fact_class=FactClass.OBSERVED, source_id=frequency_source)
            if frequency_hz is not None and frequency_source in source_ids
            else None
        ),
        demand=GridMetric(
            value=read.demand.megawatts,
            unit="MW",
            fact_class=FactClass.OBSERVED,
            source_id=demand_source,
        ),
        carbon_intensity=GridMetric(
            value=read.carbon.intensity_gco2_kwh,
            unit="gCO2/kWh",
            fact_class=FactClass.ESTIMATED,
            source_id=carbon_source,
        ),
        generation=_fuel_readings(generation_mw, fact_class=FactClass.OBSERVED, changes=changes),
        interconnectors=[
            InterconnectorFlow(
                id=flow.connector_id.lower(),
                name=flow.display_name,
                country_code=COUNTRY_CODES.get(flow.counterparty.lower(), flow.counterparty[:2].upper()),
                megawatts=flow.megawatts,
                fact_class=FactClass.OBSERVED,
            )
            for flow in read.interconnectors
        ],
        active_event=active_event,
        sources=sources,
    )


def _reading_is_stale(
    *,
    observed_at: datetime,
    source: SourceMetadataRead | None,
    requested_at: datetime,
) -> bool:
    cadence_seconds = source.expected_cadence_seconds if source else 300
    # Interval facts such as INDO and carbon are timestamped at period start,
    # then published after the period.  Two cadences plus a five-minute grace
    # captures that contract while still making minute feeds stale promptly.
    stale_after_seconds = max(600, cadence_seconds * 2 + 300)
    return (requested_at - observed_at).total_seconds() > stale_after_seconds


def _bucket(time: datetime, resolution_seconds: int) -> datetime:
    epoch = int(time.timestamp())
    bucketed = epoch - (epoch % resolution_seconds)
    return datetime.fromtimestamp(bucketed, tz=UTC)


def _latest_by_bucket[T](
    readings: Iterable[T],
    resolution_seconds: int,
    timestamp: Callable[[T], datetime],
) -> dict[datetime, T]:
    result: dict[datetime, T] = {}
    for reading in readings:
        instant = timestamp(reading)
        key = _bucket(instant, resolution_seconds)
        existing = result.get(key)
        if existing is None or timestamp(existing) < instant:
            result[key] = reading
    return result


def _latest_forecasts_by_bucket(
    readings: Iterable[ForecastRead],
    resolution_seconds: int,
    *,
    metric_type: str,
    series_keys: set[str],
) -> dict[datetime, ForecastRead]:
    result: dict[datetime, ForecastRead] = {}
    normalized_keys = {key.casefold() for key in series_keys}
    for reading in readings:
        if reading.metric_type != metric_type:
            continue
        if reading.series_key.casefold() not in normalized_keys:
            continue
        key = _bucket(reading.valid_from, resolution_seconds)
        existing = result.get(key)
        if existing is None or (reading.issued_at, reading.retrieved_at) > (
            existing.issued_at,
            existing.retrieved_at,
        ):
            result[key] = reading
    return result


def present_timeline(
    read: GridTimelineRead,
    *,
    now_boundary: datetime,
    material_gap_seconds: int = 2_700,
) -> GridTimelineResponse:
    resolution = read.resolution_seconds
    generation_by_bucket: dict[datetime, list[GenerationRead]] = defaultdict(list)
    for reading in read.generation:
        generation_by_bucket[_bucket(reading.provenance.observed_at, resolution)].append(reading)
    demand_by_bucket = _latest_by_bucket(read.demand, resolution, lambda item: item.provenance.observed_at)
    frequency_by_bucket = _latest_by_bucket(read.frequency, resolution, lambda item: item.provenance.observed_at)
    carbon_by_bucket = _latest_by_bucket(read.carbon, resolution, lambda item: item.provenance.observed_at)
    demand_forecasts = _latest_forecasts_by_bucket(
        read.forecasts,
        resolution,
        metric_type="demand",
        series_keys={"n", "national", "gb"},
    )
    carbon_forecasts = _latest_forecasts_by_bucket(
        read.forecasts,
        resolution,
        metric_type="carbon_intensity",
        series_keys={"gb", "national"},
    )

    samples: list[GridTimelineSample] = []
    latest_demand: DemandRead | None = None
    latest_carbon: CarbonRead | None = None
    latest_frequency: FrequencyRead | None = None
    latest_generation: list[GenerationRead] = []
    cursor = _bucket(read.window_start, resolution)
    end = _bucket(read.window_end, resolution)

    while cursor <= end:
        if cursor in demand_by_bucket:
            latest_demand = demand_by_bucket[cursor]
        if cursor in carbon_by_bucket:
            latest_carbon = carbon_by_bucket[cursor]
        if cursor in frequency_by_bucket:
            latest_frequency = frequency_by_bucket[cursor]
        if cursor in generation_by_bucket:
            latest_generation = generation_by_bucket[cursor]

        timestamps = [
            value.provenance.observed_at
            for value in (*latest_generation, latest_demand, latest_carbon)
            if value is not None
        ]
        newest_required_age = max(
            ((cursor - instant).total_seconds() for instant in timestamps),
            default=material_gap_seconds + 1,
        )
        if (
            cursor <= now_boundary
            and latest_demand
            and latest_carbon
            and latest_generation
            and newest_required_age <= material_gap_seconds
        ):
            generation_mw = aggregate_generation(latest_generation)
            samples.append(
                GridTimelineSample(
                    timestamp=cursor,
                    fact_class=FactClass.OBSERVED,
                    demand_mw=latest_demand.megawatts,
                    carbon_intensity=latest_carbon.intensity_gco2_kwh,
                    frequency_hz=(
                        latest_frequency.hertz
                        if latest_frequency
                        and (cursor - latest_frequency.provenance.observed_at).total_seconds() <= material_gap_seconds
                        else None
                    ),
                    generation=_fuel_readings(generation_mw, fact_class=FactClass.OBSERVED),
                )
            )
        cursor += timedelta(seconds=resolution)

    # Forward samples are emitted only when both national demand and carbon are
    # available for the same bucket.  Generation stays empty: a wind-only feed
    # is not a complete generation mix and must never be presented as one.
    forecast_cursor = _bucket(max(read.window_start, now_boundary), resolution)
    if forecast_cursor <= now_boundary:
        forecast_cursor += timedelta(seconds=resolution)
    while forecast_cursor <= end:
        demand_forecast = demand_forecasts.get(forecast_cursor)
        carbon_forecast = carbon_forecasts.get(forecast_cursor)
        if demand_forecast is not None and carbon_forecast is not None:
            samples.append(
                GridTimelineSample(
                    timestamp=forecast_cursor,
                    fact_class=FactClass.FORECAST,
                    demand_mw=demand_forecast.value,
                    carbon_intensity=carbon_forecast.value,
                    frequency_hz=None,
                    generation=[],
                )
            )
        forecast_cursor += timedelta(seconds=resolution)

    return GridTimelineResponse(
        source_resolution_seconds=resolution,
        material_gap_seconds=material_gap_seconds,
        now_boundary=now_boundary,
        samples=samples,
    )
