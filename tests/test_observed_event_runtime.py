from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.enums import EventStatus
from app.events.observed import (
    EvidenceComponentKind,
    ObservedEventEvaluator,
    ObservedEvidenceBatch,
)
from app.game.connectors import connector_registry_for_date
from app.persistence.observed_events import (
    PostgresObservedEventRepository,
    PostgresObservedEvidenceLoader,
    _frequency_component,
    _generation_component,
    _interconnector_component,
    _latest_rows_statement,
)
from app.db.models import GenerationObservation
from app.worker.contracts import PersistOutcome, PostIngestionContext
from app.worker.observed_events import (
    OBSERVED_EVENT_LOCK_NAME,
    ObservedEventMaintenanceAction,
)


NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)
CUTOFF = NOW + timedelta(minutes=1)


def _row(
    *,
    source_id: str,
    observed_at: datetime,
    source_record_id: str,
    revision: int = 0,
    retrieved_at: datetime | None = None,
    **values: Any,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid5(uuid.NAMESPACE_URL, f"{source_record_id}:r{revision}"),
        "source_id": source_id,
        "source_record_id": source_record_id,
        "observed_at": observed_at,
        "published_at": observed_at + timedelta(seconds=30),
        "retrieved_at": retrieved_at or observed_at + timedelta(minutes=1),
        "revision": revision,
        **values,
    }


def _generation_rows(*, current_wind: float = 700, revision: int = 0):
    fuels = (
        ("CCGT", "gas", 700.0, 100.0),
        ("WIND", "wind", 100.0, current_wind),
        ("SOLAR", "solar", 100.0, 100.0),
        ("NPSHYD", "hydro", 100.0, 100.0),
    )
    rows = []
    for series, fuel, previous, current in fuels:
        rows.append(
            _row(
                source_id="elexon.fuelinst",
                observed_at=NOW - timedelta(minutes=5),
                source_record_id=f"generation:previous:{series}",
                series_key=series,
                fuel_type=fuel,
                generation_mw=previous,
            )
        )
        rows.append(
            _row(
                source_id="elexon.fuelinst",
                observed_at=NOW,
                source_record_id=f"generation:current:{series}",
                revision=revision if series == "WIND" else 0,
                series_key=series,
                fuel_type=fuel,
                generation_mw=current,
            )
        )
    return rows


def _interconnector_rows():
    connectors = connector_registry_for_date(NOW.date()).expected_connector_ids
    rows = []
    for observed_at, label, flow in (
        (NOW - timedelta(minutes=10), "previous", -20.0),
        (NOW - timedelta(minutes=5), "sustained", 20.0),
        (NOW, "current", 30.0),
    ):
        for connector in connectors:
            rows.append(
                _row(
                    source_id="elexon.fuelinst",
                    observed_at=observed_at,
                    source_record_id=f"connector:{label}:{connector}",
                    connector_code=connector,
                    counterparty=connector,
                    flow_mw=flow,
                )
            )
    return rows


def _frequency_rows(value: float = 49.7, *, revision: int = 0):
    return [
        _row(
            source_id="elexon.freq",
            observed_at=NOW,
            source_record_id="frequency:current",
            revision=revision,
            series_key="gb",
            frequency_hz=value,
        )
    ]


def _batch(
    *,
    generation=True,
    interconnectors=True,
    frequency=True,
    frequency_value=49.7,
) -> ObservedEvidenceBatch:
    components = []
    if generation:
        components.append(_generation_component(_generation_rows(), cutoff_at=CUTOFF))
    if interconnectors:
        components.append(
            _interconnector_component(_interconnector_rows(), cutoff_at=CUTOFF)
        )
    if frequency:
        components.append(
            _frequency_component(
                _frequency_rows(frequency_value), cutoff_at=CUTOFF
            )
        )
    return ObservedEvidenceBatch(
        cutoff_at=CUTOFF,
        components=tuple(component for component in components if component),
    )


def test_coherent_components_evaluate_at_their_own_evidence_times() -> None:
    batch = _batch()
    assert {component.kind for component in batch.components} == {
        EvidenceComponentKind.GENERATION,
        EvidenceComponentKind.INTERCONNECTORS,
        EvidenceComponentKind.FREQUENCY,
    }

    evaluation = ObservedEventEvaluator().evaluate(batch)
    event_types = {draft.candidate.event_type for draft in evaluation.drafts}
    assert event_types == {
        "generation_leader_change",
        "renewable_share_milestone",
        "energy_position_reversal",
        "frequency_excursion",
    }
    reversal = next(
        draft
        for draft in evaluation.drafts
        if draft.candidate.event_type == "energy_position_reversal"
    )
    assert reversal.candidate.occurred_at == NOW
    assert reversal.candidate.facts[1].value == 300.0
    assert all("outage" not in draft.title.lower() for draft in evaluation.drafts)
    assert all("cause" not in draft.summary.lower() for draft in evaluation.drafts)
    assert all(len(draft.evidence_checksum) == 64 for draft in evaluation.drafts)
    assert all(draft.rule_version.startswith("observed.window-v1.") for draft in evaluation.drafts)


def test_interconnector_reversal_requires_complete_sustained_snapshots() -> None:
    incomplete = _interconnector_rows()
    incomplete.pop()
    assert _interconnector_component(incomplete, cutoff_at=CUTOFF) is None

    rows = _interconnector_rows()
    for row in rows:
        if row["observed_at"] == NOW - timedelta(minutes=5):
            row["flow_mw"] = -20.0
    component = _interconnector_component(rows, cutoff_at=CUTOFF)
    assert component is not None
    evaluation = ObservedEventEvaluator().evaluate(
        ObservedEvidenceBatch(cutoff_at=CUTOFF, components=(component,))
    )
    assert evaluation.drafts == ()


def test_partial_generation_never_resolves_generation_state() -> None:
    rows = _generation_rows()
    rows = [
        row
        for row in rows
        if not (row["observed_at"] == NOW and row["series_key"] == "NPSHYD")
    ]
    assert _generation_component(rows, cutoff_at=CUTOFF) is None

    frequency = _frequency_component(_frequency_rows(50.0), cutoff_at=CUTOFF)
    assert frequency is not None
    evaluation = ObservedEventEvaluator().evaluate(
        ObservedEvidenceBatch(cutoff_at=CUTOFF, components=(frequency,))
    )
    assert evaluation.drafts == ()
    assert [scope.event_type for scope in evaluation.stateful_scopes] == [
        "frequency_excursion"
    ]


def test_source_correction_changes_evidence_revision_without_changing_time() -> None:
    initial = _generation_component(_generation_rows(), cutoff_at=CUTOFF)
    corrected = _generation_component(
        _generation_rows(current_wind=800, revision=1), cutoff_at=CUTOFF
    )
    assert initial is not None and corrected is not None
    assert initial.window.observed_at == corrected.window.observed_at
    assert initial.evidence_fingerprint != corrected.evidence_fingerprint

    first_batch = ObservedEvidenceBatch(cutoff_at=CUTOFF, components=(initial,))
    corrected_batch = ObservedEvidenceBatch(cutoff_at=CUTOFF, components=(corrected,))
    assert first_batch.evaluation_key != corrected_batch.evaluation_key
    first = ObservedEventEvaluator().evaluate(first_batch)
    second = ObservedEventEvaluator().evaluate(corrected_batch)
    first_renewable = next(
        draft for draft in first.drafts if draft.candidate.event_type == "renewable_share_milestone"
    )
    second_renewable = next(
        draft
        for draft in second.drafts
        if draft.candidate.event_type == "renewable_share_milestone"
    )
    assert first_renewable.candidate.dedupe_key == second_renewable.candidate.dedupe_key
    assert first_renewable.evidence_checksum != second_renewable.evidence_checksum


class _MappingResult:
    def __init__(
        self,
        rows=(),
        *,
        scalar=None,
        rowcount=0,
    ) -> None:
        self.rows = list(rows)
        self.scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return list(self.rows)

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self.scalar


class _Session:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement):
        self.executed.append(statement)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_loader_uses_latest_revision_visible_at_cutoff() -> None:
    rows = _generation_rows()
    future_correction = next(
        dict(row)
        for row in rows
        if row["observed_at"] == NOW and row["series_key"] == "WIND"
    )
    future_correction.update(
        id=uuid.uuid4(),
        revision=1,
        retrieved_at=CUTOFF + timedelta(seconds=1),
        generation_mw=10.0,
    )
    session = _Session(
        [
            _MappingResult([*rows, future_correction]),
            _MappingResult(),
            _MappingResult(),
        ]
    )
    loader = PostgresObservedEvidenceLoader(lambda: session)

    batch = await loader.load(cutoff_at=CUTOFF)

    assert len(batch.components) == 1
    evaluation = ObservedEventEvaluator().evaluate(batch)
    assert "generation_leader_change" in {
        draft.candidate.event_type for draft in evaluation.drafts
    }
    sql = str(session.executed[0].compile(dialect=postgresql.dialect()))
    assert "row_number() OVER" in sql
    assert "retrieved_at <=" in sql
    assert "observed_at <=" in sql
    assert "published_at IS NULL" in sql
    interconnector_query = session.executed[1].compile(
        dialect=postgresql.dialect()
    )
    assert "elexon.fuelinst" in interconnector_query.params.values()


def test_latest_revision_statement_is_bounded_and_cutoff_first() -> None:
    statement = _latest_rows_statement(
        GenerationObservation,
        source_id="elexon.fuelinst",
        cutoff_at=CUTOFF,
        window_start=CUTOFF - timedelta(minutes=45),
        identity_fields=("source_id", "series_key", "observed_at"),
        value_fields=("series_key", "fuel_type", "generation_mw"),
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    assert 512 in compiled.params.values()
    assert "revision_rank" in str(compiled)


def _frequency_evaluation(value: float):
    component = _frequency_component(_frequency_rows(value), cutoff_at=CUTOFF)
    assert component is not None
    return ObservedEventEvaluator().evaluate(
        ObservedEvidenceBatch(cutoff_at=CUTOFF, components=(component,))
    )


@pytest.mark.asyncio
async def test_repository_insert_replay_and_correction_are_idempotent() -> None:
    first = _frequency_evaluation(49.7)
    first_draft = first.drafts[0]

    insert_session = _Session(
        [
            _MappingResult(),
            _MappingResult(scalar=uuid.uuid4()),
            _MappingResult(rowcount=0),
        ]
    )
    inserted = await PostgresObservedEventRepository(
        lambda: insert_session
    ).apply(first)
    assert inserted.inserted == 1

    stored = SimpleNamespace(
        id=uuid.uuid4(),
        deterministic_key=first_draft.candidate.dedupe_key,
        evidence_checksum=first_draft.evidence_checksum,
        evidence_version=1,
        last_observed_at=first_draft.candidate.occurred_at,
        status=EventStatus.OPEN,
    )
    replay_session = _Session(
        [_MappingResult([stored]), _MappingResult(rowcount=0)]
    )
    replayed = await PostgresObservedEventRepository(
        lambda: replay_session
    ).apply(first)
    assert replayed.unchanged == 1
    assert replayed.revised == 0

    corrected = _frequency_evaluation(49.6)
    correction_session = _Session(
        [
            _MappingResult([stored]),
            _MappingResult(rowcount=1),
            _MappingResult(rowcount=0),
        ]
    )
    revised = await PostgresObservedEventRepository(
        lambda: correction_session
    ).apply(corrected)
    assert revised.revised == 1
    update_sql = str(
        correction_session.executed[1].compile(dialect=postgresql.dialect())
    )
    assert "evidence_version" in update_sql
    assert "resolved_at" in update_sql


@pytest.mark.asyncio
async def test_repository_resolves_normal_state_and_expires_only_runtime_rules() -> None:
    normal = _frequency_evaluation(50.0)
    resolved_ids = [uuid.uuid4(), uuid.uuid4()]
    resolution_session = _Session(
        [_MappingResult(resolved_ids), _MappingResult(rowcount=2)]
    )
    resolved = await PostgresObservedEventRepository(
        lambda: resolution_session
    ).apply(normal)
    assert resolved.resolved == 2
    resolution_sql = str(
        resolution_session.executed[0].compile(dialect=postgresql.dialect())
    )
    assert "frequency_excursion" in resolution_session.executed[0].compile(
        dialect=postgresql.dialect()
    ).params.values()
    assert "rule_version LIKE" in resolution_sql
    assert 256 in resolution_session.executed[0].compile(
        dialect=postgresql.dialect()
    ).params.values()

    expired_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    expiry_session = _Session(
        [_MappingResult(expired_ids), _MappingResult(rowcount=3)]
    )
    expired = await PostgresObservedEventRepository(
        lambda: expiry_session
    ).expire(as_of=CUTOFF)
    assert expired.expired == 3
    compiled = expiry_session.executed[0].compile(dialect=postgresql.dialect())
    assert "rule_version LIKE" in str(compiled)
    assert 256 in compiled.params.values()
    assert set(
        value
        for value in compiled.params.values()
        if isinstance(value, str) and value in {
            "frequency_excursion",
            "renewable_share_milestone",
            "generation_leader_change",
            "energy_position_reversal",
        }
    ) == {
        "frequency_excursion",
        "renewable_share_milestone",
        "generation_leader_change",
        "energy_position_reversal",
    }
    assert not any("reported" in str(value) for value in compiled.params.values())


class _Locks:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.names = []

    @asynccontextmanager
    async def acquire(self, name):
        self.names.append(name)
        yield self.acquired


class _Loader:
    def __init__(self, batch):
        self.batch = batch
        self.cutoffs = []

    async def load(self, *, cutoff_at):
        self.cutoffs.append(cutoff_at)
        return self.batch


class _Store:
    def __init__(self):
        self.evaluations = []
        self.expiries = []

    async def apply(self, evaluation):
        self.evaluations.append(evaluation)
        return SimpleNamespace()

    async def expire(self, *, as_of):
        self.expiries.append(as_of)
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_maintenance_is_locked_bounded_and_replay_optimized() -> None:
    batch = _batch(generation=False, interconnectors=False)
    loader = _Loader(batch)
    store = _Store()
    locks = _Locks()
    action = ObservedEventMaintenanceAction(
        loader=loader,
        store=store,
        locks=locks,
    )
    context = PostIngestionContext(
        job_id="elexon.freq",
        completed_at=CUTOFF,
        persistence=PersistOutcome(unchanged=1),
    )

    await action.after_success(context)
    await action.after_success(context)

    assert locks.names == [OBSERVED_EVENT_LOCK_NAME, OBSERVED_EVENT_LOCK_NAME]
    assert len(store.evaluations) == 1
    assert store.expiries == [CUTOFF, CUTOFF]
    assert loader.cutoffs == [CUTOFF, CUTOFF]

    await action.after_success(
        PostIngestionContext(
            job_id="elexon.indo",
            completed_at=CUTOFF,
            persistence=PersistOutcome(),
        )
    )
    assert len(loader.cutoffs) == 2


@pytest.mark.asyncio
async def test_maintenance_skips_all_work_when_advisory_lock_is_held() -> None:
    loader = _Loader(_batch(generation=False, interconnectors=False))
    store = _Store()
    action = ObservedEventMaintenanceAction(
        loader=loader,
        store=store,
        locks=_Locks(acquired=False),
    )
    await action.after_success(
        PostIngestionContext(
            job_id="elexon.freq.reconcile",
            completed_at=CUTOFF,
            persistence=PersistOutcome(inserted=1),
        )
    )
    assert loader.cutoffs == []
    assert store.evaluations == []
    assert store.expiries == []
