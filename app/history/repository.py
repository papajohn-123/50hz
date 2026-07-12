from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CarbonObservation,
    DemandObservation,
    GenerationObservation,
    InterconnectorObservation,
)
from app.domain.enums import FactQuality
from app.history.materialize import RawMetricObservation, RawMetricSeries
from app.history.models import MetricSeriesIdentity
from app.persistence.records import canonical_source_id
from app.sources.elexon import FUEL_TYPES, INTERCONNECTOR_NAMES


MAX_HISTORY_WINDOW = timedelta(days=95)
SessionFactory = Callable[[], AsyncSession]
NATIONAL_CARBON_SOURCE_ID = canonical_source_id(
    "neso", "CARBON_INTENSITY_NATIONAL"
)
INDO_SOURCE_ID = canonical_source_id("elexon", "INDO")
FUELINST_SOURCE_ID = canonical_source_id("elexon", "FUELINST")


class HistoryMetric(StrEnum):
    NATIONAL_CARBON = "carbon.intensity.national"
    NATIONAL_DEMAND = "demand.national_outturn"
    GENERATION_FUEL = "generation.transmission_visible_by_fuel"
    INTERCONNECTOR_FLOW = "interconnector.flow"


@dataclass(frozen=True, slots=True)
class _SeriesSpec:
    model: type
    source_id: str
    geography: str
    unit: str
    fact_class: str
    methodology_version: str
    source_cadence_minutes: int
    value_attribute: str
    selector_required: bool = False


_SPECS = {
    HistoryMetric.NATIONAL_CARBON: _SeriesSpec(
        model=CarbonObservation,
        source_id=NATIONAL_CARBON_SOURCE_ID,
        geography="GB",
        unit="gCO2/kWh",
        fact_class="estimated",
        methodology_version="neso-national-carbon-v1",
        source_cadence_minutes=30,
        value_attribute="intensity_gco2_kwh",
    ),
    HistoryMetric.NATIONAL_DEMAND: _SeriesSpec(
        model=DemandObservation,
        source_id=INDO_SOURCE_ID,
        geography="GB",
        unit="MW",
        fact_class="observed",
        methodology_version="indo-national-demand-v1",
        source_cadence_minutes=30,
        value_attribute="demand_mw",
    ),
    HistoryMetric.GENERATION_FUEL: _SeriesSpec(
        model=GenerationObservation,
        source_id=FUELINST_SOURCE_ID,
        geography="GB",
        unit="MW",
        fact_class="observed",
        methodology_version="fuelinst-generation-v1",
        source_cadence_minutes=5,
        value_attribute="generation_mw",
        selector_required=True,
    ),
    HistoryMetric.INTERCONNECTOR_FLOW: _SeriesSpec(
        model=InterconnectorObservation,
        source_id=FUELINST_SOURCE_ID,
        geography="GB",
        unit="MW",
        fact_class="observed",
        methodology_version="fuelinst-interconnector-flow-v1",
        source_cadence_minutes=5,
        value_attribute="flow_mw",
        selector_required=True,
    ),
}


class HistorySeriesRequest(BaseModel):
    """A bounded request for exactly one allowlisted normalized source series."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    metric_id: HistoryMetric
    source_id: str = Field(min_length=1)
    selector: str | None = Field(default=None, min_length=1)
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("source_id", mode="before")
    @classmethod
    def normalize_source_id(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("selector", mode="before")
    @classmethod
    def normalize_selector(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("selector must be a string")
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_allowlist_and_window(self) -> "HistorySeriesRequest":
        spec = _SPECS[self.metric_id]
        if self.source_id != spec.source_id:
            raise ValueError(
                f"{self.metric_id.value} requires source_id {spec.source_id}"
            )
        if spec.selector_required and self.selector is None:
            raise ValueError(f"{self.metric_id.value} requires a selector")
        if not spec.selector_required and self.selector is not None:
            raise ValueError(f"{self.metric_id.value} does not accept a selector")
        if self.metric_id == HistoryMetric.GENERATION_FUEL:
            if self.selector not in FUEL_TYPES:
                raise ValueError("unknown FUELINST generation selector")
        if self.metric_id == HistoryMetric.INTERCONNECTOR_FLOW:
            if self.selector not in INTERCONNECTOR_NAMES:
                raise ValueError("unknown FUELINST interconnector selector")

        start = _exact_half_hour(self.start, "start")
        end = _exact_half_hour(self.end, "end")
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > MAX_HISTORY_WINDOW:
            raise ValueError("history windows cannot exceed 95 days")
        return self


class NormalizedHistoryRepository:
    """Read revision-preserving history from one normalized observation table."""

    def __init__(self, session_factory: SessionFactory) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory

    async def load(self, request: HistorySeriesRequest) -> RawMetricSeries:
        if not isinstance(request, HistorySeriesRequest):
            raise TypeError("request must be a HistorySeriesRequest")
        start = _exact_half_hour(request.start, "start")
        end = _exact_half_hour(request.end, "end")
        statement = _history_statement(request, start=start, end=end)
        async with self._session_factory() as session:
            rows = list((await session.execute(statement)).scalars().all())

        spec = _SPECS[request.metric_id]
        raw = tuple(
            _map_row(row, request=request, spec=spec, start=start, end=end)
            for row in rows
        )
        return RawMetricSeries(
            identity=_identity(request, spec),
            source_cadence_minutes=spec.source_cadence_minutes,
            observations=raw,
        )


def _history_statement(
    request: HistorySeriesRequest,
    *,
    start: datetime,
    end: datetime,
) -> Select[Any]:
    spec = _SPECS[request.metric_id]
    model = spec.model
    statement = select(model).where(
        model.source_id == request.source_id,
        model.observed_at >= start,
        model.observed_at < end,
    )

    if request.metric_id == HistoryMetric.NATIONAL_CARBON:
        statement = statement.where(
            CarbonObservation.region_code == "GB",
            CarbonObservation.quality == FactQuality.ESTIMATED,
        )
    elif request.metric_id == HistoryMetric.NATIONAL_DEMAND:
        statement = statement.where(
            DemandObservation.series_key == "gb",
            DemandObservation.demand_type == "indo",
            DemandObservation.quality.in_(
                (FactQuality.VALIDATED, FactQuality.PROVISIONAL)
            ),
        )
    elif request.metric_id == HistoryMetric.GENERATION_FUEL:
        selector = _required_selector(request)
        statement = statement.where(
            GenerationObservation.series_key == selector,
            GenerationObservation.fuel_type == FUEL_TYPES[selector],
            GenerationObservation.quality.in_(
                (FactQuality.VALIDATED, FactQuality.PROVISIONAL)
            ),
        )
    elif request.metric_id == HistoryMetric.INTERCONNECTOR_FLOW:
        statement = statement.where(
            InterconnectorObservation.connector_code == _required_selector(request),
            InterconnectorObservation.quality.in_(
                (FactQuality.VALIDATED, FactQuality.PROVISIONAL)
            ),
        )
    else:  # pragma: no cover - request validation makes this unreachable.
        raise ValueError("unknown history metric")

    return statement.order_by(
        model.observed_at.asc(),
        model.revision.asc(),
        model.id.asc(),
    )


def _map_row(
    row: Any,
    *,
    request: HistorySeriesRequest,
    spec: _SeriesSpec,
    start: datetime,
    end: datetime,
) -> RawMetricObservation:
    if not isinstance(row, spec.model):
        raise TypeError(f"query returned an unexpected {type(row).__name__} row")
    if row.source_id != request.source_id:
        raise ValueError("query returned a row from a different source")

    timestamp = _aware_utc(row.observed_at, "observed_at")
    if not start <= timestamp < end:
        raise ValueError("query returned a row outside the requested bounds")
    if isinstance(row.revision, bool) or not isinstance(row.revision, int):
        raise ValueError("observation revision must be an integer")
    if row.revision < 0:
        raise ValueError("observation revision cannot be negative")

    _validate_row_identity(row, request)
    value = float(getattr(row, spec.value_attribute))
    if not isfinite(value):
        raise ValueError("normalized history values must be finite")
    return RawMetricObservation(
        timestamp=timestamp,
        value=value,
        revision=row.revision,
        source_record_id=_source_record_provenance(row),
    )


def _validate_row_identity(row: Any, request: HistorySeriesRequest) -> None:
    if request.metric_id == HistoryMetric.NATIONAL_CARBON:
        if row.region_code != "GB" or row.quality != FactQuality.ESTIMATED:
            raise ValueError("query returned incompatible national carbon evidence")
        return
    if request.metric_id == HistoryMetric.NATIONAL_DEMAND:
        if (
            row.series_key != "gb"
            or row.demand_type != "indo"
            or row.quality not in {FactQuality.VALIDATED, FactQuality.PROVISIONAL}
        ):
            raise ValueError("query returned incompatible INDO demand evidence")
        return
    if request.metric_id == HistoryMetric.GENERATION_FUEL:
        selector = _required_selector(request)
        if (
            row.series_key != selector
            or row.fuel_type != FUEL_TYPES[selector]
            or row.quality not in {FactQuality.VALIDATED, FactQuality.PROVISIONAL}
        ):
            raise ValueError("query returned an incompatible generation fuel series")
        return
    if request.metric_id == HistoryMetric.INTERCONNECTOR_FLOW:
        if (
            row.connector_code != _required_selector(request)
            or row.quality not in {FactQuality.VALIDATED, FactQuality.PROVISIONAL}
        ):
            raise ValueError("query returned an incompatible interconnector series")
        return
    raise ValueError("unknown history metric")


def _identity(
    request: HistorySeriesRequest,
    spec: _SeriesSpec,
) -> MetricSeriesIdentity:
    metric_id = request.metric_id.value
    if request.selector is not None:
        # The selector is part of compatibility identity. Without it, two fuel
        # or connector series could be accidentally compared as one metric.
        metric_id = f"{metric_id}.{request.selector.casefold()}"
    return MetricSeriesIdentity(
        metric_id=metric_id,
        geography=spec.geography,
        unit=spec.unit,
        fact_class=spec.fact_class,
        source_id=request.source_id,
        methodology_version=spec.methodology_version,
    )


def _source_record_provenance(row: Any) -> str:
    source_record_id = (
        row.source_record_id.strip()
        if isinstance(row.source_record_id, str)
        else None
    )
    if source_record_id:
        return source_record_id
    row_id = getattr(row, "id", None)
    table_name = getattr(row, "__tablename__", type(row).__name__)
    if row_id is None:
        raise ValueError("observation requires source-record or stable row provenance")
    return f"{table_name}:row:{row_id}"


def _required_selector(request: HistorySeriesRequest) -> str:
    if request.selector is None:  # pragma: no cover - validated by the request.
        raise ValueError("this history metric requires a selector")
    return request.selector


def _exact_half_hour(value: datetime, field: str) -> datetime:
    utc = _aware_utc(value, field)
    if utc.minute not in (0, 30) or utc.second or utc.microsecond:
        raise ValueError(f"{field} must be on an exact UTC half-hour boundary")
    return utc


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)
