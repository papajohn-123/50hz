from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.assets.api import get_asset_map_service
from app.assets.api_models import AssetLifecycle
from app.assets.repository import (
    AssetCatalogRead,
    AssetEvidenceRead,
    PlannedSegmentRead,
    SettledEnergyRead,
    StoredAssetRead,
)
from app.assets.service import AssetMapService, AssetNotFoundError, _present_plan
from app.main import app


NOW = datetime(2026, 7, 15, 10, 15, tzinfo=UTC)
BM_ASSET_ID = UUID("de76e194-3dee-50cb-aa5f-f9c329fd3c77")


def _repd_row(
    source_id: str,
    name: str,
    public_id: str,
    *,
    lifecycle: str = "operational",
    latitude: float | None = 53.9,
    longitude: float | None = 1.8,
    country: str | None = "England",
) -> StoredAssetRead:
    coordinate_values = (
        {
            "eastingM": 650_000,
            "northingM": 450_000,
            "latitude": latitude,
            "longitude": longitude,
            "sourceFields": ["X-coordinate", "Y-coordinate"],
            "sourceCRS": "OSGB36 / British National Grid (EPSG:27700)",
            "outputCRS": "WGS 84 (EPSG:4326)",
            "transform": "authoritative test transform",
        }
        if latitude is not None and longitude is not None
        else None
    )
    return StoredAssetRead(
        id=UUID(int=int(source_id.removeprefix("R-"))),
        source_id="desnz.repd",
        external_id=source_id,
        asset_type="repd_site",
        display_name=name,
        fuel_type="wind",
        region_code="Yorkshire and Humber",
        counterparty=None,
        capacity_mw=50,
        latitude=latitude,
        longitude=longitude,
        active=True,
        attributes={
            "classification": "reference",
            "publicId": public_id,
            "projectName": name,
            # A malformed optional value is withheld instead of stringified.
            "operatorName": 123,
            "technology": "Wind Offshore",
            "capacityMW": 50,
            "lifecycleStatus": lifecycle,
            "sourceLifecycleStatus": lifecycle.replace("_", " ").title(),
            "isStorage": False,
            "region": "Yorkshire and Humber",
            "country": country,
            "locationStatus": (
                "source_coordinate_transformed"
                if coordinate_values is not None
                else "not_available_from_source"
            ),
            "coordinates": coordinate_values,
            "provenance": {
                "publisher": "Department for Energy Security and Net Zero",
                "dataset": "Renewable Energy Planning Database (REPD)",
                "sourceUrl": "https://assets.publishing.service.gov.uk/repd.csv",
                "licenceName": "Open Government Licence v3.0",
                "licenceUrl": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                "retrievedAt": (NOW - timedelta(days=30)).isoformat(),
            },
        },
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(minutes=1),
    )


def _bm_row(*, placeholder: bool = False) -> StoredAssetRead:
    classification = "reference_placeholder" if placeholder else "reference"
    asset_id = UUID(int=999 if placeholder else BM_ASSET_ID.int)
    display_name = "Beta Wind Farm" if placeholder else "Unhelpful primary label"
    return StoredAssetRead(
        id=asset_id,
        source_id="elexon.bm-unit-reference",
        external_id="ALPHA-1" if not placeholder else "BETA-1",
        asset_type="bm_unit",
        display_name=display_name,
        fuel_type="wind",
        region_code="_N",
        counterparty="Alpha Renewables",
        capacity_mw=50,
        latitude=None,
        longitude=None,
        active=True,
        attributes={
            "classification": classification,
            "nationalGridBmUnit": "ALPHA-1" if not placeholder else "BETA-1",
            "elexonBmUnit": "T_ALPHA-1" if not placeholder else "T_BETA-1",
            "bmUnitName": display_name,
            "fuelType": "WIND",
            "generationCapacityMW": 50,
            "locationStatus": "not_provided_by_elexon",
            "referenceVariants": (
                [
                    {
                        "nationalGridBmUnit": "ALPHA-1",
                        "elexonBmUnit": "T_ALPHA-1",
                        "bmUnitName": "Alpha Wind Farm",
                        "fuelType": "WIND",
                        "leadPartyName": "Alpha Renewables",
                        "generationCapacityMW": 50,
                        "demandCapacityMW": 0,
                        "gspGroupName": "North Scotland",
                        "eic": "48WALPHA-UNIT-1",
                    }
                ]
                if not placeholder
                else []
            ),
            "provenance": {
                "sourceId": "elexon",
                "dataset": "BM_UNIT_REFERENCE",
                "endpoint": "/reference/bmunits/all",
                "retrievedAt": (NOW - timedelta(hours=1)).isoformat(),
                "evidenceKind": "reference",
            },
        },
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(hours=1),
    )


def _evidence() -> AssetEvidenceRead:
    plan = PlannedSegmentRead(
        asset_id=BM_ASSET_ID,
        national_grid_bm_unit="ALPHA-1",
        elexon_bm_unit="T_ALPHA-1",
        settlement_date="2026-07-15",
        settlement_period=21,
        segment_start=NOW - timedelta(minutes=15),
        segment_end=NOW + timedelta(minutes=15),
        level_from_mw=-20,
        level_to_mw=80,
        retrieved_at=NOW - timedelta(minutes=5),
    )
    latest = SettledEnergyRead(
        asset_id=BM_ASSET_ID,
        national_grid_bm_unit="ALPHA-1",
        elexon_bm_unit="T_ALPHA-1",
        settlement_date="2026-07-15",
        settlement_period=20,
        interval_start=NOW - timedelta(minutes=45),
        interval_end=NOW - timedelta(minutes=15),
        energy_mwh=-10,
        average_mw=-20,
        psr_type="Generation",
        retrieved_at=NOW - timedelta(minutes=10),
        revision=2,
    )
    older = SettledEnergyRead(
        asset_id=BM_ASSET_ID,
        national_grid_bm_unit="ALPHA-1",
        elexon_bm_unit="T_ALPHA-1",
        settlement_date="2026-07-15",
        settlement_period=19,
        interval_start=NOW - timedelta(minutes=75),
        interval_end=NOW - timedelta(minutes=45),
        energy_mwh=5,
        average_mw=10,
        psr_type="Generation",
        retrieved_at=NOW - timedelta(minutes=10),
        revision=0,
    )
    return AssetEvidenceRead(plans=(plan,), settled=(older, latest))


def _batch_bm_row(index: int, name: str) -> StoredAssetRead:
    base = _bm_row()
    national_id = f"BATCH-{index:04d}"
    return StoredAssetRead(
        id=UUID(int=10_000 + index),
        source_id=base.source_id,
        external_id=national_id,
        asset_type=base.asset_type,
        display_name=name,
        fuel_type=base.fuel_type,
        region_code=base.region_code,
        counterparty=None,
        capacity_mw=base.capacity_mw,
        latitude=None,
        longitude=None,
        active=True,
        attributes={
            **base.attributes,
            "nationalGridBmUnit": national_id,
            "elexonBmUnit": f"T_{national_id}",
            "bmUnitName": name,
            "leadPartyName": None,
            "referenceVariants": [],
        },
        created_at=base.created_at,
        updated_at=base.updated_at,
    )


class FakeRepository:
    def __init__(self) -> None:
        self.catalog_calls = 0
        self.evidence_calls: list[dict[str, object]] = []
        self.catalog = AssetCatalogRead(
            repd_sites=(
                _repd_row("R-1", "Alpha Wind Farm", "repd_alpha"),
                _repd_row("R-2", "Beta Wind Farm", "repd_beta"),
                _repd_row(
                    "R-3",
                    "Future Wind Farm",
                    "repd_future",
                    lifecycle="planned",
                ),
                _repd_row(
                    "R-4",
                    "Unlocated Wind Farm",
                    "repd_unlocated",
                    latitude=None,
                    longitude=None,
                ),
            ),
            bm_units=(_bm_row(), _bm_row(placeholder=True)),
            latest_successes={"desnz.repd": NOW - timedelta(hours=1)},
        )

    async def load_catalog(self) -> AssetCatalogRead:
        self.catalog_calls += 1
        return self.catalog

    async def load_evidence(
        self,
        national_grid_bm_units=(),
        *,
        asset_ids=(),
        elexon_bm_units=(),
        evaluated_at: datetime,
        settled_per_unit: int,
    ) -> AssetEvidenceRead:
        self.evidence_calls.append(
            {
                "national": national_grid_bm_units,
                "asset_ids": asset_ids,
                "elexon": elexon_bm_units,
                "evaluated_at": evaluated_at,
                "settled_per_unit": settled_per_unit,
            }
        )
        assert asset_ids == (BM_ASSET_ID,)
        evidence = _evidence()
        return AssetEvidenceRead(
            plans=evidence.plans,
            settled=tuple(
                sorted(
                    evidence.settled,
                    key=lambda item: item.interval_end,
                    reverse=True,
                )[:settled_per_unit]
            ),
        )


class BatchingRepository:
    def __init__(self, count: int) -> None:
        names = [f"Distinctive{index:04d} Wind Farm" for index in range(count)]
        self.catalog = AssetCatalogRead(
            repd_sites=tuple(
                _repd_row(
                    f"R-{1_000 + index}",
                    name,
                    f"repd_batch_{index:04d}",
                )
                for index, name in enumerate(names)
            ),
            bm_units=tuple(
                _batch_bm_row(index, name) for index, name in enumerate(names)
            ),
            latest_successes={"desnz.repd": NOW},
        )
        self.evidence_asset_ids: list[tuple[UUID, ...]] = []

    async def load_catalog(self) -> AssetCatalogRead:
        return self.catalog

    async def load_evidence(
        self,
        national_grid_bm_units=(),
        *,
        asset_ids=(),
        elexon_bm_units=(),
        evaluated_at: datetime,
        settled_per_unit: int,
    ) -> AssetEvidenceRead:
        self.evidence_asset_ids.append(tuple(asset_ids))
        return AssetEvidenceRead(plans=(), settled=())


@pytest.mark.asyncio
async def test_map_is_filtered_bounded_source_attributed_and_truthful() -> None:
    repository = FakeRepository()
    service = AssetMapService(repository)  # type: ignore[arg-type]

    response = await service.map_assets(
        lifecycle=AssetLifecycle.OPERATIONAL,
        limit=1,
        evaluated_at=NOW,
    )

    assert response.total_count == 2
    assert response.returned_count == 1
    assert response.is_truncated is True
    assert response.source_status.state == "current"
    assert response.source_status.asset_reference_count == 1
    assert response.source_status.located_asset_count == 3
    item = response.assets[0]
    assert item.id == "repd_alpha"
    assert item.operator_name is None
    assert item.linked_bm_unit_count == 1
    assert item.coordinate.source.source_record_id == "R-1"
    assert item.coordinate.source.canonical_url.endswith(
        "renewable-energy-planning-database-quarterly-extract"
    )
    assert item.coordinate.source.licence == "Open Government Licence v3.0"
    assert "Elexon does not publish generator coordinates" in response.disclaimer
    assert item.operating_evidence is not None
    assert item.operating_evidence.has_live_metered_output is False
    assert item.operating_evidence.participant_submitted_plan is not None
    assert item.operating_evidence.participant_submitted_plan.level_mw == 30
    assert item.operating_evidence.participant_submitted_plan.direction == "export"
    assert "not actual" in item.operating_evidence.participant_submitted_plan.caveat
    assert item.operating_evidence.latest_settled_metered is not None
    assert item.operating_evidence.latest_settled_metered.direction == "import"
    assert "Delayed" in item.operating_evidence.latest_settled_metered.caveat
    assert repository.evidence_calls[0]["asset_ids"] == (BM_ASSET_ID,)
    assert repository.evidence_calls[0]["settled_per_unit"] == 1


@pytest.mark.asyncio
async def test_reference_variants_link_but_placeholders_never_count_or_link() -> None:
    repository = FakeRepository()
    service = AssetMapService(repository)  # type: ignore[arg-type]

    alpha = await service.asset_detail("repd_alpha", evaluated_at=NOW)
    beta = await service.asset_detail("repd_beta", evaluated_at=NOW)

    assert [unit.national_grid_bm_unit for unit in alpha.bm_units] == ["ALPHA-1"]
    assert alpha.bm_units[0].name == "Alpha Wind Farm"
    assert alpha.bm_units[0].elexon_bm_unit == "T_ALPHA-1"
    assert alpha.bm_units[0].match_method == "exact_site_identity"
    assert alpha.bm_units[0].match_confidence == 1
    assert len(alpha.plan) == 1
    assert [item.settlement_period for item in alpha.settled_metered] == [20, 19]
    assert all("live" in item.caveat for item in alpha.settled_metered)
    assert beta.bm_units == []
    assert beta.plan == []
    assert beta.settled_metered == []
    assert beta.asset.linked_bm_unit_count == 0
    assert any("withheld" in item for item in beta.limitations)
    assert repository.catalog_calls == 1


@pytest.mark.asyncio
async def test_cross_site_collision_with_an_unlocated_site_withholds_the_link() -> None:
    repository = FakeRepository()
    repository.catalog = AssetCatalogRead(
        repd_sites=(
            *repository.catalog.repd_sites,
            _repd_row(
                "R-5",
                "Alpha Wind Farm",
                "repd_alpha_unlocated",
                latitude=None,
                longitude=None,
            ),
        ),
        bm_units=repository.catalog.bm_units,
        latest_successes=repository.catalog.latest_successes,
    )
    service = AssetMapService(repository)  # type: ignore[arg-type]

    detail = await service.asset_detail("repd_alpha", evaluated_at=NOW)

    assert detail.asset.linked_bm_unit_count == 0
    assert detail.bm_units == []
    assert repository.evidence_calls == []
    assert any("withheld" in item for item in detail.limitations)


@pytest.mark.asyncio
async def test_unlocated_and_unknown_public_ids_fail_closed() -> None:
    service = AssetMapService(FakeRepository())  # type: ignore[arg-type]

    with pytest.raises(AssetNotFoundError):
        await service.asset_detail("repd_unlocated", evaluated_at=NOW)
    with pytest.raises(AssetNotFoundError):
        await service.asset_detail("repd_missing", evaluated_at=NOW)


@pytest.mark.asyncio
async def test_northern_ireland_sites_are_not_mixed_into_the_gb_system_map() -> None:
    repository = FakeRepository()
    repository.catalog = AssetCatalogRead(
        repd_sites=(
            *repository.catalog.repd_sites,
            _repd_row(
                "R-6",
                "Northern Ireland Wind Farm",
                "repd_ni",
                latitude=54.7,
                longitude=-6.2,
                country="Northern Ireland",
            ),
        ),
        bm_units=repository.catalog.bm_units,
        latest_successes=repository.catalog.latest_successes,
    )
    service = AssetMapService(repository)  # type: ignore[arg-type]

    response = await service.map_assets(evaluated_at=NOW)

    assert "repd_ni" not in {item.id for item in response.assets}
    assert response.source_status.located_asset_count == 3
    with pytest.raises(AssetNotFoundError):
        await service.asset_detail("repd_ni", evaluated_at=NOW)


def test_zero_participant_plan_is_presented_as_idle_without_extrapolation() -> None:
    plan = _evidence().plans[0]
    zero = PlannedSegmentRead(
        asset_id=plan.asset_id,
        national_grid_bm_unit=plan.national_grid_bm_unit,
        elexon_bm_unit=plan.elexon_bm_unit,
        settlement_date=plan.settlement_date,
        settlement_period=plan.settlement_period,
        segment_start=plan.segment_start,
        segment_end=plan.segment_end,
        level_from_mw=0,
        level_to_mw=0,
        retrieved_at=plan.retrieved_at,
    )

    presented = _present_plan(zero, evaluated_at=NOW)

    assert presented is not None
    assert presented.level_mw == 0
    assert presented.direction == "idle"


def test_catalog_cache_is_reusable_across_event_loops_without_async_locks() -> None:
    repository = FakeRepository()
    service = AssetMapService(repository)  # type: ignore[arg-type]

    asyncio.run(service.map_assets(limit=1, evaluated_at=NOW))
    asyncio.run(service.asset_detail("repd_alpha", evaluated_at=NOW))

    assert repository.catalog_calls == 1


@pytest.mark.asyncio
async def test_map_batches_more_than_500_linked_units_without_losing_links() -> None:
    repository = BatchingRepository(501)
    service = AssetMapService(repository)  # type: ignore[arg-type]

    response = await service.map_assets(limit=501, evaluated_at=NOW)

    assert response.returned_count == 501
    assert all(item.linked_bm_unit_count == 1 for item in response.assets)
    assert [len(batch) for batch in repository.evidence_asset_ids] == [500, 1]
    assert len(set().union(*(set(batch) for batch in repository.evidence_asset_ids))) == 501


@pytest.mark.asyncio
async def test_repd_delivery_state_uses_quarterly_cadence_thresholds() -> None:
    repository = FakeRepository()
    service = AssetMapService(repository)  # type: ignore[arg-type]

    repository.catalog = AssetCatalogRead(
        repd_sites=repository.catalog.repd_sites,
        bm_units=repository.catalog.bm_units,
        latest_successes={"desnz.repd": NOW - timedelta(days=92)},
    )
    delayed = await service.map_assets(evaluated_at=NOW)
    assert delayed.source_status.state == "delayed"

    service.invalidate_catalog_cache()
    repository.catalog = AssetCatalogRead(
        repd_sites=repository.catalog.repd_sites,
        bm_units=repository.catalog.bm_units,
        latest_successes={"desnz.repd": NOW - timedelta(days=183)},
    )
    stale = await service.map_assets(evaluated_at=NOW)
    assert stale.source_status.state == "stale"

    service.invalidate_catalog_cache()
    repository.catalog = AssetCatalogRead(
        repd_sites=repository.catalog.repd_sites,
        bm_units=repository.catalog.bm_units,
        latest_successes={},
    )
    unavailable = await service.map_assets(evaluated_at=NOW)
    assert unavailable.source_status.state == "unavailable"


def test_asset_routes_apply_default_filter_bounds_and_public_404() -> None:
    repository = FakeRepository()
    service = AssetMapService(repository)  # type: ignore[arg-type]
    app.dependency_overrides[get_asset_map_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.get("/v1/assets/map?limit=1")
            missing = client.get("/v1/assets/repd_missing")
            malformed = client.get(f"/v1/assets/repd_{'x' * 100}")
            oversized = client.get("/v1/assets/map?limit=5001")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["totalCount"] == 2
    assert payload["assets"][0]["lifecycle"] == "operational"
    coordinate_source = payload["assets"][0]["coordinate"]["source"]
    assert "canonicalURL" in coordinate_source
    assert "canonicalUrl" not in coordinate_source
    assert payload["assets"][0]["operatingEvidence"]["hasLiveMeteredOutput"] is False
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Located energy site not found"}
    assert malformed.status_code == 404
    assert oversized.status_code == 422
