from datetime import UTC, datetime

from app.events.models import DetectedEvent, EventCandidate, EventStatus


class InMemoryEventLifecycle:
    """Reference lifecycle used by replay tests; production persists the same transitions."""

    def __init__(self) -> None:
        self._events: dict[str, DetectedEvent] = {}

    def apply(self, candidate: EventCandidate, now: datetime | None = None) -> DetectedEvent:
        now = now or datetime.now(UTC)
        event_id = candidate.stable_id
        existing = self._events.get(event_id)
        if existing is None:
            event = DetectedEvent(
                event_id=event_id,
                revision=1,
                status=EventStatus.OPEN,
                candidate=candidate,
                opened_at=now,
                updated_at=now,
            )
        elif existing.candidate == candidate:
            return existing
        else:
            event = existing.model_copy(
                update={
                    "revision": existing.revision + 1,
                    "status": EventStatus.UPDATED,
                    "candidate": candidate,
                    "updated_at": now,
                }
            )
        self._events[event_id] = event
        return event

    def resolve(self, event_id: str, now: datetime | None = None) -> DetectedEvent:
        now = now or datetime.now(UTC)
        existing = self._events[event_id]
        resolved = existing.model_copy(
            update={
                "revision": existing.revision + 1,
                "status": EventStatus.RESOLVED,
                "updated_at": now,
                "resolved_at": now,
            }
        )
        self._events[event_id] = resolved
        return resolved

    def list(self) -> list[DetectedEvent]:
        return sorted(self._events.values(), key=lambda event: event.candidate.occurred_at, reverse=True)

