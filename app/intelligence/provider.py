from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.charging.service import CarbonForecastPoint, find_cleanest_window
from app.db.models import DetectedEvent as DetectedEventRow
from app.domain.enums import EventStatus
from app.events.models import EvidenceFact
from app.intelligence.ask import EvidenceEnvelope, GridToolProvider
from app.intelligence.models import SourceCitation
from app.persistence.reads import (
    GridReadRepository,
    GridTimelineRead,
    ReadProvenance,
    ReportedNoticeRead,
)


SessionFactory = Callable[[], AsyncSession]


class GridEventNotFoundError(LookupError):
    pass


def public_event_id(event_id: uuid.UUID) -> str:
    return f"evt_{event_id.hex}"


class DatabaseGridToolProvider(GridToolProvider):
    """Read-only, bounded evidence tools over normalized production data."""

    def __init__(
        self,
        grid_repository: GridReadRepository,
        session_factory: SessionFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.grid_repository = grid_repository
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    async def call(self, name: str, arguments: dict[str, Any]) -> EvidenceEnvelope:
        as_of = arguments.get("_as_of")
        if as_of is not None and not isinstance(as_of, datetime):
            raise ValueError("invalid trusted map time")
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("trusted map time must include a timezone")
        if name == "get_current_grid_state":
            return await self._current(as_of=as_of)
        if name == "get_metric_series":
            return await self._metric_series(
                metric=str(arguments["metric"]),
                hours=int(arguments["hours"]),
                as_of=as_of,
            )
        if name == "get_active_events":
            return await self._active_events(as_of=as_of)
        if name == "get_event_evidence":
            return await self._event_evidence(
                str(arguments["event_id"]),
                as_of=as_of,
            )
        if name == "find_cleanest_window":
            return await self._cleanest_window(
                region_code=str(arguments["region_code"]),
                duration_hours=float(arguments["duration_hours"]),
                as_of=as_of,
            )
        raise ValueError("unsupported tool")

    async def get_event_row(self, event_id: str) -> DetectedEventRow:
        statement = select(DetectedEventRow)
        parsed = _parse_public_event_id(event_id)
        if parsed is not None:
            statement = statement.where(DetectedEventRow.id == parsed)
        else:
            # This also supports deterministic event IDs used by early replay
            # fixtures without weakening the public UUID form.
            statement = statement.where(DetectedEventRow.deterministic_key == event_id)
        async with self.session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise GridEventNotFoundError(event_id)
        return row

    async def get_reported_notice(
        self, event_id: str, *, as_of: datetime | None = None
    ) -> ReportedNoticeRead:
        # The public notice ID deliberately hashes source identity rather than
        # a database UUID, so resolve it through the same canonical mapper used
        # by /v1/events.
        from app.api.notices import reported_notice_event_id

        notices = await self.grid_repository.get_active_notices(as_of=as_of)
        notice = next(
            (item for item in notices if reported_notice_event_id(item) == event_id),
            None,
        )
        if notice is None:
            raise GridEventNotFoundError(event_id)
        return notice

    async def source_citations(
        self, source_ids: set[str] | list[str]
    ) -> dict[str, SourceCitation]:
        wanted = set(source_ids)
        sources = await self.grid_repository.list_sources()
        return _citations(source for source in sources if source.id in wanted)

    async def _current(self, *, as_of: datetime | None = None) -> EvidenceEnvelope:
        wall_now = self.clock()
        requested_at = as_of or wall_now
        read = await self.grid_repository.get_current(as_of=requested_at)
        facts: list[EvidenceFact] = []
        limitations: list[str] = []
        if as_of is not None:
            if as_of <= wall_now:
                limitations.append(
                    "This historical snapshot is reconstructed from the latest "
                    "validated value at or before the selected map time; source "
                    "timestamps may differ."
                )
            else:
                limitations.append(
                    "The selected map time is in the future. These are the latest "
                    "observations available before it, not a forecast snapshot."
                )

        generation: dict[str, list[Any]] = defaultdict(list)
        for value in read.generation:
            generation[value.fuel_type.lower()].append(value)
        for fuel, values in sorted(generation.items()):
            facts.append(
                EvidenceFact(
                    fact_id=f"generation.{fuel}",
                    metric=f"{fuel}_mw",
                    label=f"{fuel} generation",
                    value=round(sum(value.megawatts for value in values), 1),
                    unit="MW",
                    observed_at=max(value.provenance.observed_at for value in values),
                    source_record_ids=_record_ids(
                        value.provenance for value in values
                    ),
                )
            )

        if read.demand is not None:
            facts.append(_fact_from_provenance(
                fact_id="demand",
                metric="demand_mw",
                label="national demand",
                value=round(read.demand.megawatts, 1),
                unit="MW",
                provenance=read.demand.provenance,
            ))
        else:
            limitations.append("National demand is unavailable.")

        if read.frequency is not None:
            facts.append(_fact_from_provenance(
                fact_id="frequency",
                metric="frequency_hz",
                label="grid frequency",
                value=round(read.frequency.hertz, 3),
                unit="Hz",
                provenance=read.frequency.provenance,
            ))
        else:
            limitations.append("Grid frequency is unavailable.")

        if read.carbon is not None:
            facts.append(_fact_from_provenance(
                fact_id="carbon",
                metric="carbon_intensity_gco2_kwh",
                label="national carbon intensity",
                value=round(read.carbon.intensity_gco2_kwh, 1),
                unit="gCO2/kWh",
                provenance=read.carbon.provenance,
            ))
        else:
            limitations.append("National carbon intensity is unavailable.")

        if read.interconnectors:
            facts.append(
                EvidenceFact(
                    fact_id="net_interconnector_flow",
                    metric="net_interconnector_flow_mw",
                    label="net interconnector flow into Britain",
                    value=round(sum(value.megawatts for value in read.interconnectors), 1),
                    unit="MW",
                    observed_at=max(
                        value.provenance.observed_at for value in read.interconnectors
                    ),
                    source_record_ids=_record_ids(
                        value.provenance for value in read.interconnectors
                    ),
                )
            )
        else:
            limitations.append("Interconnector flows are unavailable.")

        if not facts:
            raise ValueError("No validated current grid observations are available")
        observed_at = min(fact.observed_at for fact in facts)
        return EvidenceEnvelope(
            as_of=observed_at,
            freshness=_freshness(requested_at, observed_at),
            evidence_class="observed",
            facts=facts,
            source_refs=_citations(read.sources),
            limitations=limitations,
        )

    async def _metric_series(
        self, *, metric: str, hours: int, as_of: datetime | None = None
    ) -> EvidenceEnvelope:
        wall_now = self.clock()
        window_end = as_of or wall_now
        resolution = max(300, int(hours * 3_600 / 96))
        read = await self.grid_repository.get_timeline(
            window_start=window_end - timedelta(hours=hours),
            window_end=window_end,
            resolution_seconds=resolution,
        )
        facts = _series_facts(read, metric)
        limitations: list[str] = []
        if as_of is not None:
            if as_of <= wall_now:
                limitations.append(
                    "This series ends at the selected historical map time, not the live grid."
                )
            else:
                limitations.append(
                    "The series window reaches a future map time, but observed values "
                    "end at the latest available observation."
                )
        if not facts:
            limitations.append(f"No validated {metric} observations were available in the requested window.")
            evidence_at = window_end
        else:
            evidence_at = max(fact.observed_at for fact in facts)
        return EvidenceEnvelope(
            as_of=evidence_at,
            freshness=_freshness(window_end, evidence_at) if facts else "unavailable",
            evidence_class="observed",
            facts=facts[-120:],
            source_refs=_citations(read.sources),
            limitations=limitations,
        )

    async def _active_events(
        self, *, as_of: datetime | None = None
    ) -> EvidenceEnvelope:
        requested_at = as_of or self.clock()
        statement = select(DetectedEventRow)
        if as_of is None:
            statement = statement.where(
                DetectedEventRow.status.in_((EventStatus.OPEN, EventStatus.UPDATED))
            )
        else:
            statement = statement.where(
                DetectedEventRow.event_started_at <= requested_at,
                DetectedEventRow.last_observed_at <= requested_at,
                or_(
                    DetectedEventRow.resolved_at.is_(None),
                    DetectedEventRow.resolved_at > requested_at,
                ),
            )
        statement = (
            statement.order_by(DetectedEventRow.last_observed_at.desc()).limit(20)
        )
        async with self.session_factory() as session:
            events = tuple((await session.execute(statement)).scalars().all())
        notices = await self.grid_repository.get_active_notices(as_of=requested_at)

        facts: list[EvidenceFact] = []
        source_ids: set[str] = set()
        evidence_times: list[datetime] = []
        for event in events:
            source_ids.update(event.source_ids)
            evidence_times.append(event.last_observed_at)
            extracted = _event_facts(event)
            if extracted:
                facts.extend(extracted[:12])
            else:
                facts.append(
                    EvidenceFact(
                        fact_id=f"event.{event.id.hex}",
                        metric="active_event",
                        label=f"active event {public_event_id(event.id)}",
                        value=event.deterministic_summary or event.title,
                        observed_at=event.last_observed_at,
                        source_record_ids=list(event.source_ids) or [event.evidence_checksum],
                    )
                )

        for notice in notices[:20]:
            from app.api.notices import reported_notice_event_id

            source_ids.add(notice.source_id)
            evidence_times.append(notice.retrieved_at)
            identity = notice.affected_unit or notice.heading or notice.warning_type or notice.external_id
            facts.append(
                EvidenceFact(
                    fact_id=f"notice.{notice.id}.event_id",
                    metric="event_id",
                    label=f"public event identifier for {identity}",
                    value=reported_notice_event_id(notice),
                    observed_at=notice.published_at,
                    source_record_ids=[notice.external_id],
                )
            )
            facts.append(
                EvidenceFact(
                    fact_id=f"notice.{notice.id}.subject",
                    metric="reported_notice",
                    label="active reported notice",
                    value=identity,
                    observed_at=notice.published_at,
                    source_record_ids=[notice.external_id],
                )
            )
            if notice.unavailable_capacity_mw is not None:
                facts.append(
                    EvidenceFact(
                        fact_id=f"notice.{notice.id}.unavailable",
                        metric="unavailable_capacity_mw",
                        label=f"reported unavailable capacity for {identity}",
                        value=round(notice.unavailable_capacity_mw, 1),
                        unit="MW",
                        observed_at=notice.published_at,
                        source_record_ids=[notice.external_id],
                    )
                )
            if notice.reported_cause:
                facts.append(
                    EvidenceFact(
                        fact_id=f"notice.{notice.id}.cause",
                        metric="reported_cause",
                        label=f"reported cause for {identity}",
                        value=notice.reported_cause,
                        observed_at=notice.published_at,
                        source_record_ids=[notice.external_id],
                    )
                )

        limitations = [] if facts else ["No active validated events or reported notices were found."]
        if as_of is not None:
            limitations.append(
                "Events are evaluated against the selected historical map time."
            )
        evidence_at = max(evidence_times, default=requested_at)
        if source_ids:
            source_refs = await self.source_citations(source_ids)
        else:
            event_sources = (
                source
                for source in await self.grid_repository.list_sources()
                if any(
                    marker in f"{source.id} {source.dataset}".lower()
                    for marker in ("remit", "syswarn", "warning")
                )
            )
            source_refs = _citations(event_sources)
        return EvidenceEnvelope(
            as_of=evidence_at,
            freshness=_freshness(requested_at, evidence_at) if facts else "fresh",
            evidence_class="reported" if notices else "derived",
            facts=facts[:120],
            source_refs=source_refs,
            limitations=limitations,
        )

    async def _event_evidence(
        self, event_id: str, *, as_of: datetime | None = None
    ) -> EvidenceEnvelope:
        try:
            event = await self.get_event_row(event_id)
        except GridEventNotFoundError:
            notice = await self.get_reported_notice(event_id, as_of=as_of)
            return await self.reported_notice_evidence(
                notice,
                reference_time=as_of,
            )
        if as_of is not None and event.last_observed_at > as_of:
            raise GridEventNotFoundError(event_id)
        facts = _event_facts(event)
        if not facts:
            facts = [
                EvidenceFact(
                    fact_id="summary",
                    metric="event_summary",
                    label=event.title,
                    value=event.deterministic_summary or event.title,
                    observed_at=event.last_observed_at,
                    source_record_ids=list(event.source_ids) or [event.evidence_checksum],
                )
            ]
        evidence_class = str(event.evidence.get("evidence_class") or "derived")
        return EvidenceEnvelope(
            as_of=event.last_observed_at,
            freshness=_freshness(as_of or self.clock(), event.last_observed_at),
            evidence_class=evidence_class,
            facts=facts,
            source_refs=await self.source_citations(event.source_ids),
            limitations=(
                ["This is the latest event evidence known at the selected historical map time."]
                if as_of is not None
                else []
            ),
        )

    async def reported_notice_evidence(
        self,
        notice: ReportedNoticeRead,
        *,
        reference_time: datetime | None = None,
    ) -> EvidenceEnvelope:
        limitations = (
            ["The publisher has not reported a cause for this notice."]
            if notice.notice_kind != "system_warning" and not notice.reported_cause
            else []
        )
        if reference_time is not None:
            limitations.append(
                "This notice revision was active at the selected historical map time."
            )
        return EvidenceEnvelope(
            as_of=notice.published_at,
            freshness=_freshness(reference_time or self.clock(), notice.retrieved_at),
            evidence_class="reported",
            facts=_reported_notice_facts(notice),
            source_refs=await self.source_citations([notice.source_id]),
            limitations=limitations,
        )

    async def _cleanest_window(
        self,
        *,
        region_code: str,
        duration_hours: float,
        as_of: datetime | None = None,
    ) -> EvidenceEnvelope:
        wall_now = self.clock()
        window_start = as_of or wall_now
        issue_cutoff = min(window_start, wall_now)
        forecasts = await self.grid_repository.get_carbon_forecast(
            region_code=region_code,
            window_start=window_start,
            window_end=window_start + timedelta(hours=48),
            issued_before=issue_cutoff,
        )
        points = [
            CarbonForecastPoint(
                start=value.valid_from,
                end=value.valid_to or value.valid_from + timedelta(minutes=30),
                intensity_gco2_kwh=value.value,
                source_record_id=(
                    value.source_record_id
                    or f"{value.source_id}:{value.valid_from.isoformat()}"
                ),
            )
            for value in forecasts
        ]
        window = find_cleanest_window(
            points,
            duration=timedelta(hours=duration_hours),
        )
        source_ids = {value.source_id for value in forecasts}
        if window is None:
            return EvidenceEnvelope(
                as_of=max((value.issued_at for value in forecasts), default=issue_cutoff),
                freshness="unavailable",
                evidence_class="forecast",
                facts=[],
                source_refs=await self.source_citations(source_ids),
                limitations=["There is not enough contiguous forecast coverage to calculate that window."],
            )
        issued_at = max(value.issued_at for value in forecasts)
        facts = [
            EvidenceFact(
                fact_id="cleanest_window_start",
                metric="cleanest_window_start",
                label="cleanest forecast window starts",
                value=window.start.isoformat(),
                observed_at=issued_at,
                source_record_ids=window.source_record_ids,
            ),
            EvidenceFact(
                fact_id="cleanest_window_end",
                metric="cleanest_window_end",
                label="cleanest forecast window ends",
                value=window.end.isoformat(),
                observed_at=issued_at,
                source_record_ids=window.source_record_ids,
            ),
            EvidenceFact(
                fact_id="cleanest_window_intensity",
                metric="carbon_intensity_gco2_kwh",
                label="average forecast carbon intensity in that window",
                value=window.average_intensity_gco2_kwh,
                unit="gCO2/kWh",
                observed_at=issued_at,
                source_record_ids=window.source_record_ids,
            ),
        ]
        return EvidenceEnvelope(
            as_of=issued_at,
            freshness=_freshness(
                issue_cutoff,
                max(value.retrieved_at for value in forecasts),
            ),
            evidence_class="forecast",
            facts=facts,
            source_refs=await self.source_citations(source_ids),
            limitations=["This is a forecast and may change when the source publishes a new forecast."],
        )


def _parse_public_event_id(value: str) -> uuid.UUID | None:
    if not value.startswith("evt_"):
        return None
    try:
        return uuid.UUID(hex=value.removeprefix("evt_"))
    except ValueError:
        return None


def _record_ids(provenances: Any) -> list[str]:
    return list(
        dict.fromkeys(
            provenance.source_record_id
            or f"{provenance.source_id}:{provenance.observed_at.isoformat()}"
            for provenance in provenances
        )
    )


def _fact_from_provenance(
    *,
    fact_id: str,
    metric: str,
    label: str,
    value: int | float | str | bool,
    unit: str | None,
    provenance: ReadProvenance,
) -> EvidenceFact:
    return EvidenceFact(
        fact_id=fact_id,
        metric=metric,
        label=label,
        value=value,
        unit=unit,
        observed_at=provenance.observed_at,
        source_record_ids=_record_ids((provenance,)),
    )


def _citations(sources: Any) -> dict[str, SourceCitation]:
    citations: dict[str, SourceCitation] = {}
    for source in sources:
        if not source.documentation_url:
            continue
        citations[source.id] = SourceCitation(
            source_id=source.id,
            publisher=source.provider,
            title=source.display_name,
            canonical_url=source.documentation_url,
        )
    return citations


def _freshness(now: datetime, observed_at: datetime) -> str:
    age = max(timedelta(0), now - observed_at)
    if age <= timedelta(minutes=15):
        return "fresh"
    if age <= timedelta(hours=1):
        return "delayed"
    return "stale"


def _series_facts(read: GridTimelineRead, metric: str) -> list[EvidenceFact]:
    if metric == "demand_mw":
        return [
            _fact_from_provenance(
                fact_id=f"demand.{index}", metric=metric, label="national demand",
                value=round(value.megawatts, 1), unit="MW", provenance=value.provenance,
            )
            for index, value in enumerate(read.demand)
        ]
    if metric == "frequency_hz":
        return [
            _fact_from_provenance(
                fact_id=f"frequency.{index}", metric=metric, label="grid frequency",
                value=round(value.hertz, 3), unit="Hz", provenance=value.provenance,
            )
            for index, value in enumerate(read.frequency)
        ]
    if metric == "carbon_intensity_gco2_kwh":
        return [
            _fact_from_provenance(
                fact_id=f"carbon.{index}", metric=metric, label="national carbon intensity",
                value=round(value.intensity_gco2_kwh, 1), unit="gCO2/kWh", provenance=value.provenance,
            )
            for index, value in enumerate(read.carbon)
        ]

    grouped: dict[datetime, list[Any]] = defaultdict(list)
    if metric == "net_interconnector_flow_mw":
        for value in read.interconnectors:
            grouped[value.provenance.observed_at].append(value)
        label = "net interconnector flow into Britain"
        unit = "MW"
        value_of = lambda values: sum(value.megawatts for value in values)
    else:
        fuel = metric.removesuffix("_mw")
        for value in read.generation:
            if value.fuel_type.lower() == fuel:
                grouped[value.provenance.observed_at].append(value)
        label = f"{fuel} generation"
        unit = "MW"
        value_of = lambda values: sum(value.megawatts for value in values)

    return [
        EvidenceFact(
            fact_id=f"{metric}.{index}",
            metric=metric,
            label=label,
            value=round(value_of(values), 1),
            unit=unit,
            observed_at=observed_at,
            source_record_ids=_record_ids(value.provenance for value in values),
        )
        for index, (observed_at, values) in enumerate(sorted(grouped.items()))
    ]


def _event_facts(event: DetectedEventRow) -> list[EvidenceFact]:
    evidence = dict(event.evidence or {})
    raw_facts = evidence.get("facts")
    if not isinstance(raw_facts, list):
        candidate = evidence.get("candidate")
        raw_facts = candidate.get("facts") if isinstance(candidate, dict) else []
    facts: list[EvidenceFact] = []
    for raw in raw_facts or []:
        if not isinstance(raw, dict):
            continue
        try:
            facts.append(EvidenceFact.model_validate(raw))
        except ValueError:
            continue
    return facts


def _reported_notice_facts(notice: ReportedNoticeRead) -> list[EvidenceFact]:
    record_ids = [notice.external_id]
    observed_at = notice.published_at
    subject = (
        notice.affected_unit
        or notice.asset_id
        or notice.warning_type
        or notice.heading
        or notice.external_id
    )
    facts = [
        EvidenceFact(
            fact_id="notice_subject",
            metric="reported_notice_subject",
            label="reported notice subject",
            value=subject,
            observed_at=observed_at,
            source_record_ids=record_ids,
        )
    ]
    if notice.warning_text:
        facts.append(
            EvidenceFact(
                fact_id="warning_text",
                metric="reported_warning_text",
                label="reported system warning",
                value=notice.warning_text,
                observed_at=observed_at,
                source_record_ids=record_ids,
            )
        )
    for fact_id, metric, label, value in (
        (
            "normal_capacity",
            "normal_capacity_mw",
            "reported normal capacity",
            notice.normal_capacity_mw,
        ),
        (
            "available_capacity",
            "available_capacity_mw",
            "reported available capacity",
            notice.available_capacity_mw,
        ),
        (
            "unavailable_capacity",
            "unavailable_capacity_mw",
            "reported unavailable capacity",
            notice.unavailable_capacity_mw,
        ),
    ):
        if value is not None:
            facts.append(
                EvidenceFact(
                    fact_id=fact_id,
                    metric=metric,
                    label=label,
                    value=round(value, 1),
                    unit="MW",
                    observed_at=observed_at,
                    source_record_ids=record_ids,
                )
            )
    if notice.event_start is not None:
        facts.append(
            EvidenceFact(
                fact_id="event_start",
                metric="reported_event_start",
                label="reported event starts",
                value=notice.event_start.isoformat(),
                observed_at=observed_at,
                source_record_ids=record_ids,
            )
        )
    if notice.event_end is not None:
        facts.append(
            EvidenceFact(
                fact_id="event_end",
                metric="reported_event_end",
                label="reported event ends",
                value=notice.event_end.isoformat(),
                observed_at=observed_at,
                source_record_ids=record_ids,
            )
        )
    if notice.event_status:
        facts.append(
            EvidenceFact(
                fact_id="event_status",
                metric="reported_event_status",
                label="reported event status",
                value=notice.event_status,
                observed_at=observed_at,
                source_record_ids=record_ids,
            )
        )
    if notice.reported_cause:
        facts.append(
            EvidenceFact(
                fact_id="reported_cause",
                metric="reported_cause",
                label="reported cause",
                value=notice.reported_cause,
                observed_at=observed_at,
                source_record_ids=record_ids,
            )
        )
    return facts
