from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.outages.api import get_outage_repository
from app.outages.repository import DistributionIncidentRead, OutageSnapshotRead


def incident(
    reference: str,
    *,
    status: str = "unplanned",
    outward_codes: tuple[str, ...] = ("SW1A",),
    sectors: tuple[str, ...] = ("SW1A 1",),
) -> DistributionIncidentRead:
    now = datetime.now(UTC)
    return DistributionIncidentRead(
        incident_reference=reference,
        revision=2,
        status=status,
        status_id=1,
        source_created_at=now - timedelta(hours=1),
        observed_at=now - timedelta(minutes=20),
        retrieved_at=now - timedelta(minutes=18),
        incident_start=now - timedelta(hours=1),
        restored_at=(now - timedelta(minutes=5) if status == "restored" else None),
        estimated_restoration_at=now + timedelta(hours=1),
        customers_affected=37 if status != "restored" else 0,
        calls_reported=4,
        postcode_sectors=sectors,
        outward_codes=outward_codes,
        latitude=51.501,
        longitude=-0.141,
        geography_precision="aggregated_incident_point",
        operating_zone="LONDON",
        official_summary="Engineers are investigating.",
        official_details="A network fault has been reported.",
        restoration_window_text="Today 12:00 - 13:00",
        incident_category="24",
        content_sha256="a" * 64,
        last_seen_at=now - timedelta(minutes=1),
    )


class FakeRepository:
    def __init__(
        self,
        incidents: tuple[DistributionIncidentRead, ...],
        *,
        last_successful_at: datetime | None = None,
    ) -> None:
        self.incidents = incidents
        self.last_successful_at = (
            last_successful_at
            if last_successful_at is not None
            else datetime.now(UTC) - timedelta(minutes=1)
        )
        self.calls = 0

    async def load_current(
        self,
        *,
        include_restored: bool,
        hard_limit: int = 501,
    ) -> OutageSnapshotRead:
        self.calls += 1
        selected = tuple(
            item
            for item in self.incidents
            if include_restored or item.status != "restored"
        )
        return OutageSnapshotRead(
            incidents=selected[:hard_limit],
            matching_record_count=len(selected),
            snapshot_record_count=len(self.incidents),
            last_successful_at=self.last_successful_at,
        )


def request(repository: FakeRepository, method: str, path: str, **kwargs):
    app.dependency_overrides[get_outage_repository] = lambda: repository
    try:
        with TestClient(app) as client:
            return client.request(method, path, **kwargs)
    finally:
        app.dependency_overrides.clear()


def test_current_list_is_bounded_at_the_contract_and_excludes_restored_by_default() -> None:
    repository = FakeRepository(
        (
            incident("INCD-A"),
            incident("INCD-B", outward_codes=("E4",), sectors=("E4 6",)),
            incident("INCD-C", status="restored"),
        )
    )
    response = request(repository, "GET", "/v1/outages/current?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totalCount"] == 2
    assert payload["returnedCount"] == 1
    assert payload["isTruncated"] is True
    assert payload["incidents"][0]["status"] == "unplanned"
    assert payload["incidents"][0]["customersAffected"] == 37
    assert payload["incidents"][0]["customerImpactPrecision"] == "incident_aggregate"
    assert payload["incidents"][0]["location"]["precision"] == (
        "aggregated_incident_point"
    )
    assert payload["sourceStatus"]["deliveryState"] == "healthy"
    assert payload["sourceStatus"]["dataMayBePartial"] is True
    assert "particular property" in payload["disclaimer"]
    assert "householdAffected" not in response.text


def test_outward_check_matches_only_the_district_and_never_claims_property_impact() -> None:
    repository = FakeRepository(
        (
            incident("INCD-A"),
            incident("INCD-B", outward_codes=("E4",), sectors=("E4 6",)),
        )
    )
    response = request(
        repository,
        "POST",
        "/v1/outages/check",
        json={"outwardCode": " sw1a ", "limit": 10},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["outwardCode"] == "SW1A"
    assert payload["matchPrecision"] == "postcode_district"
    assert payload["householdImpact"] == "unknown"
    assert payload["districtHasReportedIncidents"] is True
    assert payload["totalCount"] == 1
    assert "Household impact remains unknown" in payload["matchStatement"]
    assert payload["incidents"][0]["postcodeSectors"] == ["SW1A 1"]


def test_outward_check_rejects_a_full_postcode_before_repository_read() -> None:
    repository = FakeRepository((incident("INCD-A"),))
    response = request(
        repository,
        "POST",
        "/v1/outages/check",
        json={"outwardCode": "SW1A 1AA"},
    )

    assert response.status_code == 422
    assert repository.calls == 0
    assert "outward code only" in response.text


def test_fresh_successful_empty_snapshot_is_not_presented_as_an_upstream_failure() -> None:
    response = request(FakeRepository(()), "GET", "/v1/outages/current")

    payload = response.json()
    assert response.status_code == 200
    assert payload["sourceStatus"]["deliveryState"] == "healthy"
    assert payload["sourceStatus"]["emptySnapshot"] is True
    assert payload["totalCount"] == 0
    assert payload["incidents"] == []


def test_stale_snapshot_remains_visible_with_an_explicit_stale_state() -> None:
    response = request(
        FakeRepository(
            (incident("INCD-A"),),
            last_successful_at=datetime.now(UTC) - timedelta(hours=1),
        ),
        "GET",
        "/v1/outages/current",
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["sourceStatus"]["deliveryState"] == "stale"
    assert payload["sourceStatus"]["deliveryAgeSeconds"] >= 3_599
    assert len(payload["incidents"]) == 1


def test_never_successful_source_is_unavailable_not_empty() -> None:
    repository = FakeRepository(())
    repository.last_successful_at = None
    response = request(repository, "GET", "/v1/outages/current")

    payload = response.json()
    assert response.status_code == 200
    assert payload["sourceStatus"]["deliveryState"] == "unavailable"
    assert payload["sourceStatus"]["lastSuccessfulAt"] is None
    assert payload["sourceStatus"]["emptySnapshot"] is False
