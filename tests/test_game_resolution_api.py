from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import app.game.api as game_api
from app.api.dependencies import get_grid_read_repository
from app.game.api import get_prediction_resolution_ledger
from app.game.models import PredictionResolution, PredictionResolutionState
from app.main import app
from app.persistence.reads import InterconnectorRead, ReadProvenance


DAY = date(2026, 7, 11)
TARGET = datetime(2026, 7, 11, 17, tzinfo=UTC)
AFTER_CLOSE = TARGET + timedelta(minutes=6)


def flow(
    connector: str,
    megawatts: float,
    *,
    observed_at: datetime = TARGET,
    retrieved_at: datetime | None = None,
    record_id: str | None = None,
) -> InterconnectorRead:
    return InterconnectorRead(
        connector_id=connector,
        display_name=connector,
        counterparty="Elsewhere",
        megawatts=megawatts,
        provenance=ReadProvenance(
            source_id="elexon.fuelinst",
            source_record_id=record_id or f"{connector}:{megawatts}",
            observed_at=observed_at,
            published_at=observed_at + timedelta(minutes=1),
            retrieved_at=retrieved_at or observed_at + timedelta(minutes=2),
        ),
    )


class ResolutionRepository:
    def __init__(self, rows: tuple[InterconnectorRead, ...] = ()) -> None:
        self.rows = rows
        self.calls: list[dict[str, object]] = []

    async def get_interconnector_observations(
        self,
        **kwargs: object,
    ) -> tuple[InterconnectorRead, ...]:
        self.calls.append(kwargs)
        return self.rows


class MemoryResolutionLedger:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, int], list[PredictionResolution]] = {}
        self.persist_calls = 0

    async def persist(
        self,
        resolution: PredictionResolution,
    ) -> PredictionResolution:
        self.persist_calls += 1
        key = (resolution.prediction_id, resolution.rule_version)
        revisions = self.rows.setdefault(key, [])
        existing = next(
            (
                item
                for item in revisions
                if item.evidence_checksum == resolution.evidence_checksum
            ),
            None,
        )
        if existing is not None:
            return existing
        persisted = resolution.model_copy(
            update={
                "resolution_revision": len(revisions) + 1,
                "is_correction": bool(revisions),
            }
        )
        revisions.append(persisted)
        return persisted


class UnavailableResolutionLedger(MemoryResolutionLedger):
    async def persist(
        self,
        resolution: PredictionResolution,
    ) -> PredictionResolution:
        raise OperationalError("SELECT 1", {}, RuntimeError("database offline"))


def request_client(
    *,
    now: datetime,
    repository: ResolutionRepository,
    ledger: MemoryResolutionLedger,
) -> TestClient:
    app.dependency_overrides[get_grid_read_repository] = lambda: repository
    app.dependency_overrides[get_prediction_resolution_ledger] = lambda: ledger
    game_api._resolution_now = lambda: now
    return TestClient(app)


@pytest.fixture(autouse=True)
def restore_app_state() -> None:
    original_clock = game_api._resolution_now
    yield
    app.dependency_overrides.clear()
    game_api._resolution_now = original_clock


def test_route_is_pending_until_close_and_has_a_stable_60_second_etag() -> None:
    repository = ResolutionRepository((flow("IFA", 800),))
    ledger = MemoryResolutionLedger()
    with request_client(
        now=TARGET + timedelta(minutes=4),
        repository=repository,
        ledger=ledger,
    ) as client:
        first = client.get(f"/v1/game/{DAY}/resolution")
        second = client.get(
            f"/v1/game/{DAY}/resolution",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert first.status_code == 200
    assert first.json()["state"] == "pending"
    assert first.json()["resolutionRevision"] == 0
    assert first.json()["isCorrection"] is False
    assert "predictionID" in first.json()
    assert "prediction_id" not in first.json()
    assert first.headers["cache-control"].startswith("public, max-age=60")
    assert second.status_code == 304
    assert second.content == b""
    assert repository.calls == []
    assert ledger.persist_calls == 0


def test_terminal_route_persists_and_exposes_a_publisher_correction() -> None:
    repository = ResolutionRepository(
        (flow("IFA", 900), flow("NEMO", -100))
    )
    ledger = MemoryResolutionLedger()
    with request_client(
        now=AFTER_CLOSE,
        repository=repository,
        ledger=ledger,
    ) as client:
        first = client.get(f"/v1/game/{DAY}/resolution")
        repository.rows = (
            flow(
                "IFA",
                -900,
                retrieved_at=TARGET + timedelta(minutes=4),
                record_id="IFA:publisher-correction",
            ),
            flow("NEMO", -100),
        )
        corrected = client.get(f"/v1/game/{DAY}/resolution")
        repeated = client.get(f"/v1/game/{DAY}/resolution")

    assert first.status_code == 200
    assert first.json()["state"] == "resolved"
    assert first.json()["outcome"] == "importing"
    assert first.json()["resolutionRevision"] == 1
    assert first.json()["isCorrection"] is False
    assert corrected.status_code == 200
    assert corrected.json()["state"] == "resolved"
    assert corrected.json()["outcome"] == "exporting"
    assert corrected.json()["resolutionRevision"] == 2
    assert corrected.json()["isCorrection"] is True
    assert repeated.json() == corrected.json()
    assert ledger.persist_calls == 3
    query = repository.calls[0]
    assert query["source_id"] == "elexon.fuelinst"
    assert query["window_start"] == TARGET - timedelta(minutes=5)
    assert query["window_end"] == TARGET + timedelta(minutes=5)


def test_closed_window_without_compatible_evidence_is_audited_as_void() -> None:
    repository = ResolutionRepository()
    ledger = MemoryResolutionLedger()
    with request_client(
        now=AFTER_CLOSE,
        repository=repository,
        ledger=ledger,
    ) as client:
        response = client.get(f"/v1/game/{DAY}/resolution")

    assert response.status_code == 200
    assert response.json()["state"] == "void"
    assert response.json()["outcome"] is None
    assert response.json()["coverage"] == {
        "expectedConnectorCount": 0,
        "observedConnectorCount": 0,
        "coverageFraction": 0.0,
        "complete": False,
    }
    assert response.json()["resolutionRevision"] == 1
    assert ledger.persist_calls == 1


def test_database_failure_is_reported_as_a_retryable_service_error() -> None:
    repository = ResolutionRepository((flow("IFA", 800),))
    ledger = UnavailableResolutionLedger()
    with request_client(
        now=AFTER_CLOSE,
        repository=repository,
        ledger=ledger,
    ) as client:
        response = client.get(f"/v1/game/{DAY}/resolution")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.json() == {
        "detail": "Prediction evidence is temporarily unavailable"
    }


@pytest.mark.parametrize(
    ("day", "now", "target", "window_start", "window_end"),
    [
        (
            date(2026, 1, 15),
            datetime(2026, 1, 15, 18, 6, tzinfo=UTC),
            datetime(2026, 1, 15, 18, tzinfo=UTC),
            datetime(2026, 1, 15, 17, 55, tzinfo=UTC),
            datetime(2026, 1, 15, 18, 5, tzinfo=UTC),
        ),
        (
            date(2026, 7, 15),
            datetime(2026, 7, 15, 17, 6, tzinfo=UTC),
            datetime(2026, 7, 15, 17, tzinfo=UTC),
            datetime(2026, 7, 15, 16, 55, tzinfo=UTC),
            datetime(2026, 7, 15, 17, 5, tzinfo=UTC),
        ),
    ],
)
def test_route_uses_1800_europe_london_across_dst(
    day: date,
    now: datetime,
    target: datetime,
    window_start: datetime,
    window_end: datetime,
) -> None:
    repository = ResolutionRepository()
    ledger = MemoryResolutionLedger()
    with request_client(now=now, repository=repository, ledger=ledger) as client:
        response = client.get(f"/v1/game/{day}/resolution")

    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["targetAt"]) == target
    assert repository.calls[0]["window_start"] == window_start
    assert repository.calls[0]["window_end"] == window_end


@pytest.mark.parametrize("requested", ["2026-06-09", "2026-07-12"])
def test_route_rejects_dates_outside_the_bounded_resolution_window(
    requested: str,
) -> None:
    repository = ResolutionRepository()
    ledger = MemoryResolutionLedger()
    with request_client(
        now=AFTER_CLOSE,
        repository=repository,
        ledger=ledger,
    ) as client:
        response = client.get(f"/v1/game/{requested}/resolution")

    assert response.status_code == 422
    assert "Prediction date must be between" in response.json()["detail"]
    assert repository.calls == []
    assert ledger.persist_calls == 0


def test_openapi_publishes_the_camel_case_resolution_contract() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/v1/game/{date}/resolution"]["get"]
    model = schema["components"]["schemas"]["PredictionResolution"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PredictionResolution"
    }
    assert {
        "predictionID",
        "observedValueMW",
        "sourceIDs",
        "sourceRevisionKeys",
        "resolutionRevision",
        "isCorrection",
    }.issubset(model["properties"])
    assert "prediction_id" not in model["properties"]
