from dataclasses import dataclass
from datetime import datetime

from app.events.models import EventCandidate
from app.events.rules import (
    frequency_excursion,
    generation_leader_change,
    interconnector_reversal,
    renewable_share_milestone,
)


@dataclass(frozen=True, slots=True)
class GridObservationWindow:
    observed_at: datetime
    previous_generation_mw: dict[str, float]
    current_generation_mw: dict[str, float]
    previous_net_import_mw: float | None
    current_net_import_mw: float | None
    net_flow_sustained_samples: int
    frequency_hz: float | None
    generation_source_record_ids: list[str]
    interconnector_source_record_ids: list[str]
    frequency_source_record_ids: list[str]


class EventProcessor:
    """Pure event evaluation for one normalized observation window."""

    def evaluate(self, window: GridObservationWindow) -> list[EventCandidate]:
        candidates: list[EventCandidate | None] = [
            generation_leader_change(
                window.previous_generation_mw,
                window.current_generation_mw,
                window.observed_at,
                window.generation_source_record_ids,
            ),
            renewable_share_milestone(
                window.current_generation_mw,
                window.observed_at,
                window.generation_source_record_ids,
            ),
        ]
        if window.previous_net_import_mw is not None and window.current_net_import_mw is not None:
            candidates.append(
                interconnector_reversal(
                    window.previous_net_import_mw,
                    window.current_net_import_mw,
                    window.observed_at,
                    window.interconnector_source_record_ids,
                    sustained_samples=window.net_flow_sustained_samples,
                )
            )
        if window.frequency_hz is not None:
            candidates.append(
                frequency_excursion(
                    window.frequency_hz,
                    window.observed_at,
                    window.frequency_source_record_ids,
                )
            )
        return [candidate for candidate in candidates if candidate is not None]

