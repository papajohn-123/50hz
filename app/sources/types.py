"""Source-neutral ingestion contracts.

These types deliberately live outside the database package.  A source adapter can
therefore be tested, replayed, or used by a one-off backfill without importing an
ORM model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Generic, Mapping, Protocol, TypeVar, runtime_checkable


RecordT = TypeVar("RecordT")


def as_utc(value: datetime, *, field_name: str = "datetime") -> datetime:
    """Return an aware UTC datetime and reject ambiguous naive values."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """Half-open source query window: ``start <= value < end`` internally.

    Upstream APIs may treat their upper query bound as inclusive.  Idempotent
    source keys and database upserts are therefore still required.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", as_utc(self.start, field_name="start"))
        object.__setattr__(self, "end", as_utc(self.end, field_name="end"))
        if self.start >= self.end:
            raise ValueError("observation window start must be before end")


class FlowDirection(StrEnum):
    IMPORT = "import"
    EXPORT = "export"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    source_key: str
    observed_at: datetime
    published_at: datetime
    retrieved_at: datetime
    fuel_code: str
    fuel_type: str
    generation_mw: float
    settlement_date: date | None = None
    settlement_period: int | None = None
    source: str = "elexon"
    dataset: str = "FUELINST"


@dataclass(frozen=True, slots=True)
class DemandRecord:
    source_key: str
    observed_at: datetime
    published_at: datetime
    retrieved_at: datetime
    demand_mw: float
    settlement_date: date | None = None
    settlement_period: int | None = None
    source: str = "elexon"
    dataset: str = "INDO"


@dataclass(frozen=True, slots=True)
class FrequencyRecord:
    source_key: str
    observed_at: datetime
    retrieved_at: datetime
    frequency_hz: float
    published_at: datetime | None = None
    source: str = "elexon"
    dataset: str = "FREQ"


@dataclass(frozen=True, slots=True)
class InterconnectorFlowRecord:
    """A signed interconnector flow using the 50Hz convention.

    Positive ``flow_mw`` means electricity enters Great Britain.  Negative
    ``flow_mw`` means Great Britain exports electricity.
    """

    source_key: str
    observed_at: datetime
    published_at: datetime
    retrieved_at: datetime
    interconnector_id: str
    interconnector_name: str
    flow_mw: float
    settlement_date: date | None = None
    settlement_period: int | None = None
    source: str = "elexon"
    dataset: str = "FUELINST"

    @property
    def direction(self) -> FlowDirection:
        if self.flow_mw > 0:
            return FlowDirection.IMPORT
        if self.flow_mw < 0:
            return FlowDirection.EXPORT
        return FlowDirection.IDLE

    @property
    def import_mw(self) -> float:
        return max(self.flow_mw, 0.0)

    @property
    def export_mw(self) -> float:
        return max(-self.flow_mw, 0.0)


@dataclass(frozen=True, slots=True)
class AdapterResult(Generic[RecordT]):
    """Normalized records plus enough raw provenance for an auditable write."""

    source_id: str
    dataset: str
    endpoint: str
    window: ObservationWindow
    retrieved_at: datetime
    request_url: str
    records: tuple[RecordT, ...]
    raw_payload: Any
    raw_body: bytes
    checksum_sha256: str
    content_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@runtime_checkable
class SourceAdapter(Protocol[RecordT]):
    source_id: str
    dataset: str
    endpoint: str

    async def fetch(self, window: ObservationWindow) -> AdapterResult[RecordT]:
        """Fetch and normalize every source record in ``window``."""

