from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.models import (
    CarbonObservation,
    DemandObservation,
    FrequencyObservation,
    GenerationObservation,
    InterconnectorObservation,
    SourceMetadata,
)
from app.domain.enums import FactQuality
from app.persistence.locks import PostgresAdvisoryLockProvider, advisory_lock_key
from app.persistence.reads import GridReadRepository


NOW = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)


class FakeScalars:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class FakeResult:
    def __init__(self, values: list[Any] | None = None) -> None:
        self.values = values or []

    def scalars(self) -> FakeScalars:
        return FakeScalars(self.values)

    def scalar_one(self) -> Any:
        assert len(self.values) == 1
        return self.values[0]

    def scalar_one_or_none(self) -> Any | None:
        assert len(self.values) <= 1
        return self.values[0] if self.values else None


class FakeSession:
    def __init__(self, responses: list[FakeResult]) -> None:
        self.responses = list(responses)
        self.executed: list[tuple[Any, Any]] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: Any, parameters: Any = None) -> FakeResult:
        self.executed.append((statement, parameters))
        assert self.responses, "unexpected query"
        return self.responses.pop(0)


def common_observation(at: datetime, source_id: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "raw_payload_id": None,
        "source_record_id": f"record:{at.isoformat()}",
        "observed_at": at,
        "published_at": at + timedelta(seconds=10),
        "retrieved_at": at + timedelta(seconds=20),
        "revision": 0,
        "quality": FactQuality.VALIDATED,
        "attributes": {},
    }


def generation(at: datetime, value: float, fuel: str = "wind") -> GenerationObservation:
    return GenerationObservation(
        **common_observation(at, "elexon.fuelinst"),
        series_key=fuel.upper(),
        fuel_type=fuel,
        asset_id=None,
        generation_mw=value,
        settlement_date=None,
        settlement_period=None,
    )


def source() -> SourceMetadata:
    return SourceMetadata(
        id="elexon.fuelinst",
        provider="elexon",
        dataset="FUELINST",
        display_name="Elexon Insights — FUELINST",
        base_url="https://data.elexon.co.uk",
        documentation_url="https://bmrs.elexon.co.uk/api-documentation",
        licence_name="Elexon data terms",
        licence_url="https://www.elexon.co.uk/about/copyright/",
        attribution="Data supplied by Elexon Limited.",
        expected_cadence_seconds=120,
        active=True,
    )


def test_current_read_combines_latest_source_neutral_rows() -> None:
    generation_row = generation(NOW - timedelta(minutes=5), 13_500)
    demand_row = DemandObservation(
        **common_observation(NOW - timedelta(minutes=4), "elexon.indo"),
        series_key="gb",
        demand_type="indo",
        demand_mw=28_000,
        settlement_date=None,
        settlement_period=None,
    )
    frequency_row = FrequencyObservation(
        **common_observation(NOW - timedelta(minutes=3), "elexon.freq"),
        series_key="gb",
        frequency_hz=49.987,
    )
    interconnector_row = InterconnectorObservation(
        **{
            **common_observation(NOW - timedelta(minutes=2), "elexon.fuelinst"),
            "attributes": {"displayName": "IFA"},
        },
        connector_code="INTFR",
        asset_id=None,
        counterparty="France",
        flow_mw=750,
    )
    carbon_row = CarbonObservation(
        **common_observation(NOW - timedelta(minutes=1), "neso.national"),
        region_code="GB",
        intensity_gco2_kwh=91,
        index_label="low",
        generation_mix=[],
    )
    source_rows = [
        source(),
        SourceMetadata(
            id="elexon.indo",
            provider="elexon",
            dataset="INDO",
            display_name="Elexon Insights — INDO",
            base_url="https://data.elexon.co.uk",
            expected_cadence_seconds=120,
            active=True,
        ),
        SourceMetadata(
            id="elexon.freq",
            provider="elexon",
            dataset="FREQ",
            display_name="Elexon Insights — FREQ",
            base_url="https://data.elexon.co.uk",
            expected_cadence_seconds=60,
            active=True,
        ),
        SourceMetadata(
            id="neso.national",
            provider="neso",
            dataset="NATIONAL",
            display_name="NESO — NATIONAL",
            base_url="https://api.carbonintensity.org.uk",
            expected_cadence_seconds=1800,
            active=True,
        ),
    ]
    session = FakeSession(
        [
            FakeResult([generation_row]),
            FakeResult([demand_row]),
            FakeResult([frequency_row]),
            FakeResult([interconnector_row]),
            FakeResult([carbon_row]),
            FakeResult(source_rows),
        ]
    )
    repository = GridReadRepository(lambda: session, clock=lambda: NOW)

    current = asyncio.run(repository.get_current())

    assert current.requested_at == NOW
    assert current.generation[0].fuel_type == "wind"
    assert current.demand is not None and current.demand.megawatts == 28_000
    assert current.frequency is not None and current.frequency.hertz == 49.987
    assert current.interconnectors[0].display_name == "IFA"
    assert current.interconnectors[0].megawatts == 750
    assert current.carbon is not None and current.carbon.intensity_gco2_kwh == 91
    assert current.effective_at == carbon_row.observed_at
    assert current.retrieved_at == carbon_row.retrieved_at
    assert {item.id for item in current.sources} == {
        "elexon.fuelinst",
        "elexon.indo",
        "elexon.freq",
        "neso.national",
    }


def test_timeline_downsamples_each_series_without_inventing_bucket_timestamps() -> None:
    first = generation(NOW + timedelta(seconds=1), 100)
    same_bucket_later = generation(NOW + timedelta(seconds=50), 200)
    next_bucket = generation(NOW + timedelta(seconds=61), 300)
    session = FakeSession(
        [
            FakeResult([first, same_bucket_later, next_bucket]),
            FakeResult(),
            FakeResult(),
            FakeResult(),
            FakeResult(),
            FakeResult([source()]),
        ]
    )
    repository = GridReadRepository(lambda: session)

    timeline = asyncio.run(
        repository.get_timeline(
            window_start=NOW,
            window_end=NOW + timedelta(minutes=2),
            resolution_seconds=60,
        )
    )

    assert [row.megawatts for row in timeline.generation] == [200, 300]
    assert [row.provenance.observed_at for row in timeline.generation] == [
        same_bucket_later.observed_at,
        next_bucket.observed_at,
    ]
    assert timeline.resolution_seconds == 60


def test_advisory_lock_key_is_stable_signed_bigint() -> None:
    key = advisory_lock_key("50hz:ingest:elexon")

    assert key == advisory_lock_key("50hz:ingest:elexon")
    assert key != advisory_lock_key("50hz:ingest:neso")
    assert -(2**63) <= key < 2**63


def test_advisory_lock_is_released_on_context_exit() -> None:
    session = FakeSession([FakeResult([True]), FakeResult([True])])
    provider = PostgresAdvisoryLockProvider(lambda: session)

    async def scenario() -> None:
        async with provider.acquire("50hz:ingest:elexon") as acquired:
            assert acquired is True
            assert len(session.executed) == 1

    asyncio.run(scenario())

    assert len(session.executed) == 2
    assert "pg_try_advisory_lock" in str(session.executed[0][0])
    assert "pg_advisory_unlock" in str(session.executed[1][0])
    assert session.executed[0][1] == session.executed[1][1]

