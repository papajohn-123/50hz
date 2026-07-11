from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.dependencies import get_grid_read_repository
from app.main import app
from app.persistence.reads import (
    CarbonRead,
    CurrentGridRead,
    DemandRead,
    FrequencyRead,
    ForecastRead,
    GenerationRead,
    GridTimelineRead,
    InterconnectorRead,
    ReadProvenance,
    ReportedNoticeRead,
    SourceMetadataRead,
)


OBSERVED = datetime(2026, 7, 11, 12, tzinfo=UTC)
RETRIEVED = datetime(2026, 7, 11, 12, 1, tzinfo=UTC)


def provenance(source_id: str) -> ReadProvenance:
    return ReadProvenance(source_id, f"{source_id}:1", OBSERVED, OBSERVED, RETRIEVED)


SOURCES = (
    SourceMetadataRead("elexon.fuelinst", "elexon", "FUELINST", "Elexon", "https://example.test", None, "Elexon", 300),
    SourceMetadataRead("elexon.indo", "elexon", "INDO", "Elexon", "https://example.test", None, "Elexon", 300),
    SourceMetadataRead("elexon.freq", "elexon", "FREQ", "Elexon", "https://example.test", None, "Elexon", 60),
    SourceMetadataRead("neso.carbon", "neso", "CARBON", "NESO", "https://example.test", None, "NESO", 1_800),
)


class FakeRepository:
    def current(self, requested_at: datetime) -> CurrentGridRead:
        return CurrentGridRead(
            requested_at=requested_at,
            generation=(
                GenerationRead("WIND", "wind", 8_000, provenance("elexon.fuelinst")),
                GenerationRead("CCGT", "gas", 4_000, provenance("elexon.fuelinst")),
            ),
            demand=DemandRead("gb", "indo", 14_000, provenance("elexon.indo")),
            frequency=FrequencyRead("gb", 50.01, provenance("elexon.freq")),
            interconnectors=(InterconnectorRead("INTFR", "IFA", "France", -500, provenance("elexon.fuelinst")),),
            carbon=CarbonRead("GB", 75, "low", (), provenance("neso.carbon")),
            sources=SOURCES,
        )

    async def get_current(self, *, as_of: datetime | None = None, carbon_region: str = "GB") -> CurrentGridRead:
        return self.current(as_of or RETRIEVED)

    async def get_timeline(self, *, window_start: datetime, window_end: datetime, resolution_seconds: int, carbon_region: str = "GB") -> GridTimelineRead:
        current = self.current(window_end)
        return GridTimelineRead(
            window_start=window_start,
            window_end=window_end,
            resolution_seconds=resolution_seconds,
            generation=current.generation,
            demand=(current.demand,),
            frequency=(current.frequency,),
            interconnectors=current.interconnectors,
            carbon=(current.carbon,),
            sources=SOURCES,
        )

    async def list_sources(self) -> tuple[SourceMetadataRead, ...]:
        return SOURCES

    async def get_active_notices(
        self,
        *,
        as_of: datetime | None = None,
        warning_fresh_for_seconds: int = 900,
    ) -> tuple[ReportedNoticeRead, ...]:
        return ()

    async def get_carbon_forecast(
        self,
        *,
        region_code: str,
        window_start: datetime,
        window_end: datetime,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]:
        return ()


class EventFakeRepository(FakeRepository):
    async def get_active_notices(
        self,
        *,
        as_of: datetime | None = None,
        warning_fresh_for_seconds: int = 900,
    ) -> tuple[ReportedNoticeRead, ...]:
        return (
            ReportedNoticeRead(
                id="notice-row-1",
                source_id="elexon.syswarn",
                notice_kind="system_warning",
                external_id="syswarn:1",
                revision_key="checksum-1",
                revision_number=None,
                published_at=OBSERVED - timedelta(minutes=1),
                retrieved_at=OBSERVED,
                event_start=None,
                event_end=None,
                heading=None,
                event_type=None,
                event_status=None,
                affected_unit=None,
                asset_id=None,
                fuel_type=None,
                normal_capacity_mw=None,
                available_capacity_mw=None,
                unavailable_capacity_mw=None,
                reported_cause=None,
                reported_related_information=None,
                warning_type="System Warning",
                warning_text="A system warning has been reported.",
                evidence={"classification": "reported"},
            ),
        )


def client() -> TestClient:
    app.dependency_overrides[get_grid_read_repository] = lambda: FakeRepository()
    return TestClient(app)


def test_current_route_matches_native_acronym_keys() -> None:
    with client() as test_client:
        response = test_client.get("/v1/grid/current")
    app.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["frequency"]["sourceID"] == "elexon.freq"
    assert payload["generation"][0]["share"] <= 1


def test_current_route_includes_highest_priority_reported_event() -> None:
    app.dependency_overrides[get_grid_read_repository] = lambda: EventFakeRepository()
    try:
        with TestClient(app) as test_client:
            response = test_client.get("/v1/grid/current")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    event = response.json()["activeEvent"]
    assert event["title"] == "System Warning"
    assert event["evidenceClass"] == "reported"
    assert response.json()["freshness"] == "critical"


def test_timeline_route_accepts_native_query_contract() -> None:
    with client() as test_client:
        response = test_client.get(
            "/v1/grid/timeline",
            params={
                "from": "2026-07-11T11:00:00Z",
                "to": "2026-07-11T13:00:00Z",
                "resolution": 1_800,
            },
        )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["sourceResolutionSeconds"] == 1_800


def test_sources_route_exposes_attribution() -> None:
    with client() as test_client:
        response = test_client.get("/v1/sources")
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()[0]["attribution"]


def test_daily_game_route_uses_live_data_availability_contract() -> None:
    with client() as test_client:
        response = test_client.get("/v1/game/today")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(response.json()["missions"]) == 3
    assert isinstance(response.json()["source_fresh"], bool)
