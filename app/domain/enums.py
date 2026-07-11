from enum import StrEnum


class DataClassification(StrEnum):
    """How a fact came to exist, independent of its quality."""

    OBSERVED = "observed"
    REPORTED = "reported"
    DERIVED = "derived"
    FORECAST = "forecast"


class FactQuality(StrEnum):
    VALIDATED = "validated"
    PROVISIONAL = "provisional"
    ESTIMATED = "estimated"


class FreshnessState(StrEnum):
    FRESH = "fresh"
    DELAYED = "delayed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class FlowDirection(StrEnum):
    IMPORT = "import"
    EXPORT = "export"
    NEUTRAL = "neutral"


class IngestionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class EventStatus(StrEnum):
    OPEN = "open"
    UPDATED = "updated"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class EventSeverity(StrEnum):
    INFO = "info"
    NOTABLE = "notable"
    MATERIAL = "material"
    CRITICAL = "critical"


class EvidenceConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AUTHORITATIVE = "authoritative"

