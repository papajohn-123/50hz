"""Immutable, source-neutral contracts for individually addressable grid assets.

The contracts keep three importantly different kinds of evidence separate:

* reference data describes an asset, but is not an operating measurement;
* a planned profile is a participant-submitted intention, never an outturn; and
* settled metered energy is an observed half-hour quantity published with delay.

That distinction is deliberately encoded in data rather than left to UI copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Generic, TypeVar

from app.domain.settlement import settlement_period_at


RecordT = TypeVar("RecordT")


class EvidenceKind(StrEnum):
    REFERENCE = "reference"
    REPORTED_PLAN = "reported_plan"
    SETTLED_METERED = "settled_metered"


class PowerDirection(StrEnum):
    """Direction under the BM-unit signed-level convention."""

    EXPORT = "export"
    IMPORT = "import"
    ZERO = "zero"


def direction_for_level(level_mw: float) -> PowerDirection:
    if level_mw > 0:
        return PowerDirection.EXPORT
    if level_mw < 0:
        return PowerDirection.IMPORT
    return PowerDirection.ZERO


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class Provenance:
    source_id: str
    dataset: str
    endpoint: str
    retrieved_at: datetime
    evidence_kind: EvidenceKind
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        object.__setattr__(self, "dataset", _required_text(self.dataset, "dataset"))
        object.__setattr__(self, "endpoint", _required_text(self.endpoint, "endpoint"))
        object.__setattr__(
            self,
            "retrieved_at",
            _utc(self.retrieved_at, "retrieved_at"),
        )
        if self.published_at is not None:
            object.__setattr__(
                self,
                "published_at",
                _utc(self.published_at, "published_at"),
            )


@dataclass(frozen=True, slots=True)
class GeoPoint:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")


@dataclass(frozen=True, slots=True)
class AssetReference:
    """Reference metadata for a grid asset, with no implied live state.

    ``asset_id`` is the source's stable national identifier.  ``location`` is
    optional by design: callers must not turn a GSP group, unit name, or party
    address into a fabricated map coordinate.
    """

    asset_id: str
    source_asset_id: str | None
    display_name: str | None
    fuel_type: str | None
    lead_party_name: str | None
    lead_party_id: str | None
    asset_type: str | None
    production_or_consumption: str | None
    submits_physical_notifications: bool | None
    generation_capacity_mw: float | None
    demand_capacity_mw: float | None
    gsp_group_id: str | None
    gsp_group_name: str | None
    interconnector_id: str | None
    eic: str | None
    location: GeoPoint | None
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _required_text(self.asset_id, "asset_id"))
        if self.provenance.evidence_kind is not EvidenceKind.REFERENCE:
            raise ValueError("asset reference provenance must be reference evidence")


@dataclass(frozen=True, slots=True)
class PlannedProfileSegment:
    """One participant-submitted linear Physical Notification segment."""

    asset_id: str
    source_asset_id: str
    settlement_date: date
    settlement_period: int
    start: datetime
    end: datetime
    level_from_mw: float
    level_to_mw: float
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _required_text(self.asset_id, "asset_id"))
        object.__setattr__(
            self,
            "source_asset_id",
            _required_text(self.source_asset_id, "source_asset_id"),
        )
        object.__setattr__(self, "start", _utc(self.start, "start"))
        object.__setattr__(self, "end", _utc(self.end, "end"))
        if self.start >= self.end:
            raise ValueError("planned segment start must precede end")
        period = settlement_period_at(self.settlement_date, self.settlement_period)
        if self.start < period.start_utc or self.end > period.end_utc:
            raise ValueError("planned segment must stay within its settlement period")
        if self.provenance.evidence_kind is not EvidenceKind.REPORTED_PLAN:
            raise ValueError("planned segment provenance must be reported-plan evidence")

    def level_at(self, instant: datetime, *, include_end: bool = False) -> float | None:
        """Linearly interpolate within this segment without extrapolating."""

        instant_utc = _utc(instant, "instant")
        if instant_utc < self.start or instant_utc > self.end:
            return None
        if instant_utc == self.end and not include_end:
            return None
        elapsed = (instant_utc - self.start).total_seconds()
        duration = (self.end - self.start).total_seconds()
        fraction = elapsed / duration
        return self.level_from_mw + fraction * (self.level_to_mw - self.level_from_mw)


@dataclass(frozen=True, slots=True)
class PlannedProfile:
    asset_id: str
    source_asset_id: str
    settlement_date: date
    settlement_period: int
    segments: tuple[PlannedProfileSegment, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _required_text(self.asset_id, "asset_id"))
        object.__setattr__(
            self,
            "source_asset_id",
            _required_text(self.source_asset_id, "source_asset_id"),
        )
        if not self.segments:
            raise ValueError("planned profile must contain at least one segment")
        previous: PlannedProfileSegment | None = None
        for segment in self.segments:
            identity = (
                segment.asset_id,
                segment.source_asset_id,
                segment.settlement_date,
                segment.settlement_period,
            )
            if identity != (
                self.asset_id,
                self.source_asset_id,
                self.settlement_date,
                self.settlement_period,
            ):
                raise ValueError("planned profile contains a segment for another asset or period")
            if previous is not None and segment.start < previous.end:
                raise ValueError("planned profile segments cannot overlap")
            previous = segment

    @property
    def retrieved_at(self) -> datetime:
        return max(segment.provenance.retrieved_at for segment in self.segments)

    def level_at(self, instant: datetime) -> "PlannedOperatingLevel | None":
        """Return the submitted plan at an instant, or ``None`` across a gap.

        Segment intervals are start-inclusive/end-exclusive.  In particular,
        the period end belongs to the next settlement period; treating the final
        submitted point as the operating level beyond that boundary would be
        extrapolation.
        """

        instant_utc = _utc(instant, "instant")
        for segment in self.segments:
            level = segment.level_at(instant_utc)
            if level is None:
                continue
            return PlannedOperatingLevel(
                asset_id=self.asset_id,
                source_asset_id=self.source_asset_id,
                settlement_date=self.settlement_date,
                settlement_period=self.settlement_period,
                at=instant_utc,
                level_mw=level,
                provenance=segment.provenance,
            )
        return None


@dataclass(frozen=True, slots=True)
class PlannedOperatingLevel:
    """An interpolated reported plan.  It is explicitly not actual output."""

    asset_id: str
    source_asset_id: str
    settlement_date: date
    settlement_period: int
    at: datetime
    level_mw: float
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _utc(self.at, "at"))
        if self.provenance.evidence_kind is not EvidenceKind.REPORTED_PLAN:
            raise ValueError("planned level provenance must be reported-plan evidence")

    @property
    def direction(self) -> PowerDirection:
        return direction_for_level(self.level_mw)


@dataclass(frozen=True, slots=True)
class SettledMeteredEnergy:
    """Delayed settled energy for one half-hour, not instantaneous output.

    Elexon's B1610 is first published around five days after operation and may
    later be refreshed by subsequent settlement runs.  ``average_mw`` is only a
    duration-normalized representation of the MWh quantity.
    """

    asset_id: str
    source_asset_id: str
    settlement_date: date
    settlement_period: int
    interval_start: datetime
    interval_end: datetime
    energy_mwh: float
    psr_type: str | None
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _required_text(self.asset_id, "asset_id"))
        object.__setattr__(
            self,
            "source_asset_id",
            _required_text(self.source_asset_id, "source_asset_id"),
        )
        object.__setattr__(
            self,
            "interval_start",
            _utc(self.interval_start, "interval_start"),
        )
        object.__setattr__(self, "interval_end", _utc(self.interval_end, "interval_end"))
        if self.interval_end - self.interval_start != timedelta(minutes=30):
            raise ValueError("settled metered energy must cover one half-hour")
        period = settlement_period_at(self.settlement_date, self.settlement_period)
        if (self.interval_start, self.interval_end) != (period.start_utc, period.end_utc):
            raise ValueError("metered interval does not match its settlement period")
        if self.provenance.evidence_kind is not EvidenceKind.SETTLED_METERED:
            raise ValueError("metered energy provenance must be settled-metered evidence")

    @property
    def average_mw(self) -> float:
        duration_hours = (self.interval_end - self.interval_start).total_seconds() / 3600
        return self.energy_mwh / duration_hours

    @property
    def direction(self) -> PowerDirection:
        return direction_for_level(self.average_mw)

    @property
    def age_at_retrieval(self) -> timedelta:
        return self.provenance.retrieved_at - self.interval_end


@dataclass(frozen=True, slots=True)
class ParsedBatch(Generic[RecordT]):
    records: tuple[RecordT, ...]
    warnings: tuple[str, ...] = ()
