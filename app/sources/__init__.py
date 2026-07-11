"""Public source-adapter surface."""

from app.sources.client import AsyncJSONClient, RetryPolicy
from app.sources.elexon import (
    FreqAdapter,
    FuelInstAdapter,
    FuelInstGenerationAdapter,
    IndoAdapter,
    InitialDemandAdapter,
    InterconnectorFlowAdapter,
    SystemFrequencyAdapter,
)
from app.sources.types import (
    AdapterResult,
    DemandRecord,
    FlowDirection,
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    ObservationWindow,
    SourceAdapter,
)

__all__ = [
    "AdapterResult",
    "AsyncJSONClient",
    "DemandRecord",
    "FlowDirection",
    "FreqAdapter",
    "FrequencyRecord",
    "FuelInstAdapter",
    "FuelInstGenerationAdapter",
    "GenerationRecord",
    "IndoAdapter",
    "InitialDemandAdapter",
    "InterconnectorFlowAdapter",
    "InterconnectorFlowRecord",
    "ObservationWindow",
    "RetryPolicy",
    "SourceAdapter",
    "SystemFrequencyAdapter",
]

