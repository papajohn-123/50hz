from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import ForecastObservation, GenerationObservation
from app.persistence.ingestion import (
    ImmutableRevisionConflictError,
    _FORECAST_SPEC,
    _GENERATION_SPEC,
    _insert_immutable_revisions,
    _load_latest_revisions,
    _prepare_immutable_revisions,
)
from app.persistence.records import map_generation_record
from app.sources.types import GenerationRecord


NOW = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
RAW_1 = UUID("ff3a54be-d16e-4648-b119-fdb8b429e144")
RAW_2 = UUID("ad74cb75-fe92-421e-a20b-79dbe0d7ac82")


def generation_values(
    value: float,
    *,
    raw_payload_id: UUID = RAW_1,
    retrieved_at: datetime = NOW,
    observed_at: datetime = NOW - timedelta(minutes=5),
) -> dict[str, Any]:
    record = GenerationRecord(
        source_key=f"elexon:FUELINST:{observed_at.isoformat()}:WIND",
        observed_at=observed_at,
        published_at=observed_at + timedelta(minutes=1),
        retrieved_at=retrieved_at,
        fuel_code="WIND",
        fuel_type="wind",
        generation_mw=value,
    )
    return map_generation_record(
        record,
        source_id="elexon.fuelinst",
        raw_payload_id=raw_payload_id,
    )


def forecast_values(
    value: float,
    *,
    raw_payload_id: UUID = RAW_1,
    retrieved_at: datetime = NOW,
) -> dict[str, Any]:
    return {
        "source_id": "elexon.ndf",
        "raw_payload_id": raw_payload_id,
        "source_record_id": "ndf:2026-07-11T12:00:00Z:2026-07-11T18:00:00Z",
        "metric_type": "demand",
        "series_key": "gb",
        "variant": "point",
        "value": value,
        "unit": "MW",
        "value_low": None,
        "value_high": None,
        "valid_from": datetime(2026, 7, 11, 18, tzinfo=UTC),
        "valid_to": None,
        "issued_at": datetime(2026, 7, 11, 12, tzinfo=UTC),
        "published_at": datetime(2026, 7, 11, 12, tzinfo=UTC),
        "retrieved_at": retrieved_at,
        "revision": 0,
        "model_name": "NDF",
        "settlement_date": None,
        "settlement_period": None,
        "attributes": {"classification": "forecast", "dataset": "NDF"},
    }


def test_unchanged_repoll_does_not_allocate_a_revision_for_delivery_metadata() -> None:
    original_values = generation_values(13_500)
    original = GenerationObservation(**original_values)
    repoll = generation_values(
        13_500,
        raw_payload_id=RAW_2,
        retrieved_at=NOW + timedelta(minutes=2),
    )
    identity = (
        original.source_id,
        original.series_key,
        original.observed_at,
    )

    prepared, inserted, corrected, unchanged = _prepare_immutable_revisions(
        _GENERATION_SPEC,
        [repoll],
        {identity: original},
    )

    assert prepared == []
    assert (inserted, corrected, unchanged) == (0, 0, 1)
    assert original.raw_payload_id == RAW_1
    assert original.retrieved_at == NOW
    assert original.revision == 0


def test_source_correction_appends_next_revision_and_retains_original() -> None:
    original = GenerationObservation(**generation_values(13_500))
    correction = generation_values(
        13_750,
        raw_payload_id=RAW_2,
        retrieved_at=NOW + timedelta(minutes=2),
    )
    identity = (
        original.source_id,
        original.series_key,
        original.observed_at,
    )

    prepared, inserted, corrected, unchanged = _prepare_immutable_revisions(
        _GENERATION_SPEC,
        [correction],
        {identity: original},
    )

    assert (inserted, corrected, unchanged) == (0, 1, 0)
    assert prepared[0]["revision"] == 1
    assert prepared[0]["generation_mw"] == 13_750
    assert original.revision == 0
    assert original.generation_mw == 13_500
    assert original.raw_payload_id == RAW_1


def test_same_vintage_forecast_correction_appends_revision_one() -> None:
    original = ForecastObservation(**forecast_values(28_000))
    correction = forecast_values(
        28_400,
        raw_payload_id=RAW_2,
        retrieved_at=NOW + timedelta(minutes=3),
    )
    identity = (
        original.source_id,
        original.metric_type,
        original.series_key,
        original.variant,
        original.valid_from,
        original.issued_at,
    )

    prepared, inserted, corrected, unchanged = _prepare_immutable_revisions(
        _FORECAST_SPEC,
        [correction],
        {identity: original},
    )

    assert (inserted, corrected, unchanged) == (0, 1, 0)
    assert prepared[0]["revision"] == 1
    assert prepared[0]["value"] == 28_400
    assert original.revision == 0
    assert original.value == 28_000


class _Scalars:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class _Result:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self) -> _Scalars:
        return _Scalars(self._values)


class _Session:
    def __init__(self, responses: list[list[Any]]) -> None:
        self._responses = list(responses)
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result(self._responses.pop(0))


def test_revision_insert_conflict_never_falls_back_to_an_overwrite() -> None:
    session = _Session([[]])
    candidate = generation_values(13_500)
    candidate["revision"] = 0

    with pytest.raises(ImmutableRevisionConflictError, match="source lock"):
        asyncio.run(
            _insert_immutable_revisions(session, _GENERATION_SPEC, [candidate])
        )

    sql = str(
        session.statements[0].compile(dialect=postgresql.dialect())
    ).upper()
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert "DO UPDATE" not in sql


def test_latest_revision_lookup_is_bounded_instead_of_n_plus_one() -> None:
    rows = [
        generation_values(
            float(index),
            observed_at=NOW + timedelta(minutes=index),
        )
        for index in range(251)
    ]
    session = _Session([[], []])

    result = asyncio.run(_load_latest_revisions(session, _GENERATION_SPEC, rows))

    assert result == {}
    assert len(session.statements) == 2
    assert all(
        " IN "
        in str(statement.compile(dialect=postgresql.dialect())).upper()
        for statement in session.statements
    )
