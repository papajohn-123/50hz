"""Truth-preserving presentation of source-located generation sites.

DESNZ REPD supplies the map geography.  Elexon supplies a separate BM-unit
reference catalogue and, where a conservative source-backed link is unique,
participant plans and delayed settled energy.  This module deliberately never
turns an Elexon name, party, or GSP group into a coordinate.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.assets.api_models import (
    AssetCoordinateResponse,
    AssetDetailResponse,
    AssetFeedState,
    AssetFeedStatusResponse,
    AssetLifecycle,
    AssetMapItemResponse,
    AssetMapResponse,
    AssetOperatingEvidenceResponse,
    AssetPlanEvidenceResponse,
    AssetSettledEvidenceResponse,
    AssetSourceResponse,
    BMUnitSummaryResponse,
)
from app.assets.linking import (
    AssetLinkDecision,
    AssetLinkMethod,
    link_repd_sites,
)
from app.assets.models import AssetReference, EvidenceKind, Provenance
from app.assets.repository import (
    AssetCatalogRead,
    AssetCatalogRepository,
    AssetEvidenceRead,
    PlannedSegmentRead,
    SettledEnergyRead,
    StoredAssetRead,
)
from app.geography.records import (
    REPD_EXPECTED_CADENCE_SECONDS,
    REPD_SOURCE_ATTRIBUTION,
)
from app.geography.repd import (
    DEFAULT_TRANSFORM_NAME,
    OSGB36_BNG_CRS,
    REPD_DATASET_NAME,
    REPD_LICENCE_NAME,
    REPD_LICENCE_URL,
    REPD_PUBLICATION_URL,
    REPD_PUBLISHER,
    REPDCoordinates,
    REPDProvenance,
    REPDSite,
    REPDStatus,
    WGS84_CRS,
)


CATALOG_CACHE_SECONDS = 5 * 60
DETAIL_SETTLED_INTERVAL_LIMIT = 12
EVIDENCE_QUERY_UNIT_LIMIT = 500
REPD_SOURCE_ID = "desnz.repd"
GREAT_BRITAIN_COUNTRIES = frozenset({"england", "scotland", "wales"})

MAP_BOUNDARY = (
    "Active, source-located renewable energy and storage sites retained from "
    "the latest DESNZ Renewable Energy Planning Database extract for Great "
    "Britain; lifecycle filtering uses REPD's published status."
)
MAP_DISCLAIMER = (
    "REPD is quarterly planning/reference data, not a complete conventional "
    "generator register. Elexon does not publish generator coordinates; plan "
    "or settled evidence appears only after one unique high-confidence "
    "source-backed BM-unit link and is never live unit output."
)
PLAN_CAVEAT = (
    "Participant-submitted Physical Notification plan, linearly interpolated "
    "within the reported segment; not actual or metered output."
)
SETTLED_CAVEAT = (
    "Delayed Elexon B1610 settled half-hour energy; average power is energy "
    "divided by 0.5 hours and is not live output."
)


class AssetNotFoundError(LookupError):
    """The public ID does not resolve to a uniquely located REPD site."""


@dataclass(frozen=True, slots=True)
class _SiteEntry:
    stored: StoredAssetRead
    site: REPDSite
    public_id: str | None

    @property
    def is_located(self) -> bool:
        return self.site.coordinates is not None

    @property
    def is_in_great_britain(self) -> bool:
        country = _optional_text(self.site.country)
        return (
            country is not None
            and country.casefold() in GREAT_BRITAIN_COUNTRIES
        )


@dataclass(frozen=True, slots=True)
class _ReferenceEntry:
    stored: StoredAssetRead
    reference: AssetReference


@dataclass(frozen=True, slots=True)
class _CatalogSnapshot:
    catalog: AssetCatalogRead
    sites: tuple[_SiteEntry, ...]
    references: tuple[_ReferenceEntry, ...]
    links_by_source_id: Mapping[str, AssetLinkDecision]
    sites_by_public_id: Mapping[str, _SiteEntry]
    bm_rows_by_national_id: Mapping[str, StoredAssetRead]
    authoritative_reference_count: int
    located_site_count: int


class AssetMapService:
    """Compose the public asset map from separate authoritative datasets.

    The immutable catalogue/link result is cached for five minutes.  Cache
    replacement is intentionally lock-free: a rare concurrent miss may repeat
    one read, but no asyncio primitive is retained across event loops (which is
    important for test clients and multi-loop hosting).
    """

    def __init__(
        self,
        repository: AssetCatalogRepository,
        *,
        cache_ttl_seconds: float = CATALOG_CACHE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        self._repository = repository
        self._cache_ttl_seconds = cache_ttl_seconds
        self._monotonic = monotonic
        self._cached_snapshot: _CatalogSnapshot | None = None
        self._cache_expires_at = 0.0

    async def map_assets(
        self,
        *,
        lifecycle: AssetLifecycle = AssetLifecycle.OPERATIONAL,
        limit: int = 5_000,
        evaluated_at: datetime | None = None,
    ) -> AssetMapResponse:
        if not 1 <= limit <= 5_000:
            raise ValueError("asset map limit must be between 1 and 5000")
        instant = _utc(evaluated_at or datetime.now(UTC), "evaluated_at")
        snapshot = await self._load_snapshot()

        matching = tuple(
            sorted(
                (
                    entry
                    for entry in snapshot.sites
                    if entry.public_id is not None
                    and entry.is_located
                    and entry.is_in_great_britain
                    and _asset_lifecycle(entry.site.status) is lifecycle
                    and snapshot.sites_by_public_id.get(entry.public_id) is entry
                ),
                key=lambda entry: (
                    entry.site.project_name.casefold(),
                    entry.public_id or "",
                ),
            )
        )
        selected = matching[:limit]
        evidence = await self._load_evidence_for_sites(
            snapshot,
            selected,
            evaluated_at=instant,
            settled_per_unit=1,
        )
        plans_by_asset, settled_by_asset = _evidence_by_asset(
            evidence,
            evaluated_at=instant,
        )
        assets = [
            self._present_map_item(
                snapshot,
                entry,
                evaluated_at=instant,
                plans=plans_by_asset,
                settled=settled_by_asset,
            )
            for entry in selected
        ]
        return AssetMapResponse(
            evaluated_at=instant,
            source_status=_source_status(snapshot, evaluated_at=instant),
            total_count=len(matching),
            returned_count=len(assets),
            is_truncated=len(assets) < len(matching),
            assets=assets,
            boundary=MAP_BOUNDARY,
            disclaimer=MAP_DISCLAIMER,
        )

    async def asset_detail(
        self,
        public_id: str,
        *,
        evaluated_at: datetime | None = None,
    ) -> AssetDetailResponse:
        instant = _utc(evaluated_at or datetime.now(UTC), "evaluated_at")
        normalized_id = public_id.strip() if isinstance(public_id, str) else ""
        snapshot = await self._load_snapshot()
        entry = snapshot.sites_by_public_id.get(normalized_id)
        if (
            entry is None
            or not entry.is_located
            or not entry.is_in_great_britain
        ):
            raise AssetNotFoundError("Located energy site not found")

        decision = snapshot.links_by_source_id.get(entry.site.source_id)
        bm_row = _linked_bm_row(snapshot, decision)
        if bm_row is None:
            evidence = AssetEvidenceRead(plans=(), settled=())
        else:
            evidence = await self._repository.load_evidence(
                asset_ids=(bm_row.id,),
                evaluated_at=instant,
                settled_per_unit=DETAIL_SETTLED_INTERVAL_LIMIT,
            )
        plans_by_asset, settled_by_asset = _evidence_by_asset(
            evidence,
            evaluated_at=instant,
        )
        map_item = self._present_map_item(
            snapshot,
            entry,
            evaluated_at=instant,
            plans=plans_by_asset,
            settled=settled_by_asset,
        )
        presented_plans = (
            [
                item
                for row in plans_by_asset.get(bm_row.id, ())
                if (item := _present_plan(row, evaluated_at=instant)) is not None
            ]
            if bm_row is not None
            else []
        )
        presented_settled = (
            [
                item
                for row in settled_by_asset.get(bm_row.id, ())
                if (item := _present_settled(row)) is not None
            ]
            if bm_row is not None
            else []
        )
        bm_units: list[BMUnitSummaryResponse] = []
        if decision is not None and decision.is_linked and bm_row is not None:
            reference = _selected_reference(snapshot.references, decision)
            if reference is not None:
                bm_units.append(_present_bm_unit(reference, decision))

        return AssetDetailResponse(
            evaluated_at=instant,
            asset=map_item,
            bm_units=bm_units,
            plan=presented_plans,
            settled_metered=presented_settled,
            limitations=_detail_limitations(linked=bool(bm_units)),
        )

    def invalidate_catalog_cache(self) -> None:
        self._cached_snapshot = None
        self._cache_expires_at = 0.0

    async def _load_snapshot(self) -> _CatalogSnapshot:
        now = self._monotonic()
        cached = self._cached_snapshot
        if cached is not None and now < self._cache_expires_at:
            return cached

        catalog = await self._repository.load_catalog()
        snapshot = _build_snapshot(catalog)
        # Publish the value only after it is complete.  Concurrent rebuilds are
        # equivalent because all inputs and sort/link policies are deterministic.
        self._cached_snapshot = snapshot
        self._cache_expires_at = self._monotonic() + self._cache_ttl_seconds
        return snapshot

    async def _load_evidence_for_sites(
        self,
        snapshot: _CatalogSnapshot,
        sites: Sequence[_SiteEntry],
        *,
        evaluated_at: datetime,
        settled_per_unit: int,
    ) -> AssetEvidenceRead:
        asset_ids = tuple(
            dict.fromkeys(
                row.id
                for entry in sites
                if (
                    row := _linked_bm_row(
                        snapshot,
                        snapshot.links_by_source_id.get(entry.site.source_id),
                    )
                )
                is not None
            )
        )
        if not asset_ids:
            return AssetEvidenceRead(plans=(), settled=())
        reads = []
        for offset in range(0, len(asset_ids), EVIDENCE_QUERY_UNIT_LIMIT):
            reads.append(
                await self._repository.load_evidence(
                    asset_ids=asset_ids[offset : offset + EVIDENCE_QUERY_UNIT_LIMIT],
                    evaluated_at=evaluated_at,
                    settled_per_unit=settled_per_unit,
                )
            )
        return AssetEvidenceRead(
            plans=tuple(row for read in reads for row in read.plans),
            settled=tuple(row for read in reads for row in read.settled),
        )

    def _present_map_item(
        self,
        snapshot: _CatalogSnapshot,
        entry: _SiteEntry,
        *,
        evaluated_at: datetime,
        plans: Mapping[UUID, tuple[PlannedSegmentRead, ...]],
        settled: Mapping[UUID, tuple[SettledEnergyRead, ...]],
    ) -> AssetMapItemResponse:
        coordinates = entry.site.coordinates
        public_id = entry.public_id
        if coordinates is None or public_id is None:
            raise ValueError("map items require a located public REPD site")
        decision = snapshot.links_by_source_id.get(entry.site.source_id)
        bm_row = _linked_bm_row(snapshot, decision)
        plan = None
        latest_settled = None
        if bm_row is not None:
            plan = next(
                (
                    presented
                    for row in plans.get(bm_row.id, ())
                    if (
                        presented := _present_plan(row, evaluated_at=evaluated_at)
                    )
                    is not None
                ),
                None,
            )
            latest_settled = next(
                (
                    presented
                    for row in settled.get(bm_row.id, ())
                    if (presented := _present_settled(row)) is not None
                ),
                None,
            )

        operating_evidence = None
        if bm_row is not None:
            operating_evidence = AssetOperatingEvidenceResponse(
                participant_submitted_plan=plan,
                latest_settled_metered=latest_settled,
                has_live_metered_output=False,
            )
        return AssetMapItemResponse(
            id=public_id,
            name=entry.site.project_name,
            operator_name=entry.site.operator_name,
            technology=entry.site.technology,
            fuel_type=_optional_text(entry.stored.fuel_type) or "other",
            lifecycle=_asset_lifecycle(entry.site.status),
            capacity_mw=entry.site.capacity_mw,
            region=entry.site.region,
            country=entry.site.country,
            coordinate=AssetCoordinateResponse(
                latitude=coordinates.latitude,
                longitude=coordinates.longitude,
                precision="repd_site_point_osgb36_to_wgs84",
                source=_site_source(entry.site),
            ),
            linked_bm_unit_count=1 if bm_row is not None else 0,
            operating_evidence=operating_evidence,
        )


def _build_snapshot(catalog: AssetCatalogRead) -> _CatalogSnapshot:
    sites = tuple(
        entry
        for row in catalog.repd_sites
        if (entry := _rehydrate_site(row)) is not None
    )
    authoritative_rows = tuple(
        row
        for row in catalog.bm_units
        if _optional_text(row.attributes.get("classification")) == "reference"
    )
    references = tuple(
        entry
        for row in authoritative_rows
        for entry in _rehydrate_reference_entries(row)
    )
    decisions = link_repd_sites(
        tuple(entry.site for entry in sites),
        tuple(entry.reference for entry in references),
    )
    links_by_source_id = {
        entry.site.source_id: decision
        for entry, decision in zip(sites, decisions, strict=True)
    }

    public_id_counts = Counter(
        entry.public_id for entry in sites if entry.public_id is not None
    )
    sites_by_public_id = {
        entry.public_id: entry
        for entry in sites
        if entry.public_id is not None and public_id_counts[entry.public_id] == 1
    }
    bm_rows_by_national_id: dict[str, StoredAssetRead] = {}
    for entry in references:
        bm_rows_by_national_id.setdefault(entry.reference.asset_id, entry.stored)

    return _CatalogSnapshot(
        catalog=catalog,
        sites=sites,
        references=references,
        links_by_source_id=links_by_source_id,
        sites_by_public_id=sites_by_public_id,
        bm_rows_by_national_id=bm_rows_by_national_id,
        authoritative_reference_count=len(authoritative_rows),
        located_site_count=sum(
            entry.is_located
            and entry.is_in_great_britain
            and entry.public_id is not None
            and sites_by_public_id.get(entry.public_id) is entry
            for entry in sites
        ),
    )


def _rehydrate_site(row: StoredAssetRead) -> _SiteEntry | None:
    attributes = row.attributes
    if _optional_text(attributes.get("classification")) != "reference":
        return None
    lifecycle_text = _optional_text(attributes.get("lifecycleStatus"))
    try:
        status = REPDStatus(lifecycle_text)
    except (TypeError, ValueError):
        return None
    provenance_values = _as_mapping(attributes.get("provenance"))
    retrieved_at = _optional_datetime(provenance_values.get("retrievedAt"))
    if retrieved_at is None:
        return None
    project_name = (
        _optional_text(attributes.get("projectName"))
        or _optional_text(row.display_name)
    )
    if project_name is None:
        return None
    technology = _optional_text(attributes.get("technology")) or "Not stated in REPD"
    public_id = _optional_text(attributes.get("publicId"))
    if public_id is not None and (
        not public_id.startswith("repd_")
        or len(public_id) > 80
        or "/" in public_id
        or "?" in public_id
        or "#" in public_id
    ):
        public_id = None

    coordinates = _rehydrate_coordinates(row, attributes)
    source_url = _optional_text(provenance_values.get("sourceUrl")) or REPD_PUBLICATION_URL
    provenance = REPDProvenance(
        publisher=REPD_PUBLISHER,
        dataset=REPD_DATASET_NAME,
        source_url=source_url,
        licence_name=REPD_LICENCE_NAME,
        licence_url=REPD_LICENCE_URL,
        retrieved_at=retrieved_at,
    )
    site = REPDSite(
        source_id=row.external_id,
        project_name=project_name,
        operator_name=_optional_text(attributes.get("operatorName")),
        technology=technology,
        capacity_mw=_nonnegative_float(
            attributes.get("capacityMW"),
            row.capacity_mw,
        ),
        status=status,
        source_status=(
            _optional_text(attributes.get("sourceLifecycleStatus"))
            or status.value.replace("_", " ").title()
        ),
        storage_type=_optional_text(attributes.get("storageType")),
        is_storage=(
            attributes.get("isStorage")
            if isinstance(attributes.get("isStorage"), bool)
            else row.fuel_type == "storage"
        ),
        region=(
            _optional_text(attributes.get("region"))
            or _optional_text(row.region_code)
        ),
        country=_optional_text(attributes.get("country")),
        planning_authority=_optional_text(attributes.get("planningAuthority")),
        record_last_updated=_optional_text(attributes.get("recordLastUpdated")),
        coordinates=coordinates,
        provenance=provenance,
    )
    return _SiteEntry(stored=row, site=site, public_id=public_id)


def _rehydrate_coordinates(
    row: StoredAssetRead,
    attributes: Mapping[str, Any],
) -> REPDCoordinates | None:
    if _optional_text(attributes.get("locationStatus")) != "source_coordinate_transformed":
        return None
    latitude = _finite_float(row.latitude)
    longitude = _finite_float(row.longitude)
    if latitude is None or longitude is None:
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    values = _as_mapping(attributes.get("coordinates"))
    easting = _finite_float(values.get("eastingM"))
    northing = _finite_float(values.get("northingM"))
    nested_latitude = _finite_float(values.get("latitude"))
    nested_longitude = _finite_float(values.get("longitude"))
    source_fields = values.get("sourceFields")
    if (
        easting is None
        or northing is None
        or not isinstance(source_fields, (list, tuple))
        or len(source_fields) != 2
        or any(_optional_text(value) is None for value in source_fields)
    ):
        return None
    if nested_latitude is not None and not math.isclose(
        nested_latitude,
        latitude,
        abs_tol=1e-6,
    ):
        return None
    if nested_longitude is not None and not math.isclose(
        nested_longitude,
        longitude,
        abs_tol=1e-6,
    ):
        return None
    try:
        return REPDCoordinates(
            easting_m=easting,
            northing_m=northing,
            latitude=latitude,
            longitude=longitude,
            source_fields=tuple(str(value).strip() for value in source_fields),
            source_crs=_optional_text(values.get("sourceCRS")) or OSGB36_BNG_CRS,
            output_crs=_optional_text(values.get("outputCRS")) or WGS84_CRS,
            transform=_optional_text(values.get("transform")) or DEFAULT_TRANSFORM_NAME,
        )
    except ValueError:
        return None


def _rehydrate_reference_entries(
    row: StoredAssetRead,
) -> tuple[_ReferenceEntry, ...]:
    attributes = row.attributes
    provenance_values = _as_mapping(attributes.get("provenance"))
    retrieved_at = _optional_datetime(provenance_values.get("retrievedAt"))
    if retrieved_at is None:
        return ()
    provenance = Provenance(
        source_id="elexon",
        dataset="BM_UNIT_REFERENCE",
        endpoint="/reference/bmunits/all",
        retrieved_at=retrieved_at,
        evidence_kind=EvidenceKind.REFERENCE,
        published_at=_optional_datetime(provenance_values.get("publishedAt")),
    )
    variants: list[Mapping[str, Any]] = [attributes]
    raw_variants = attributes.get("referenceVariants")
    if isinstance(raw_variants, (list, tuple)):
        variants.extend(
            value for value in raw_variants if isinstance(value, Mapping)
        )

    entries: list[_ReferenceEntry] = []
    seen: set[tuple[Any, ...]] = set()
    for index, values in enumerate(variants):
        reference = _rehydrate_reference(
            row,
            values,
            provenance=provenance,
            use_row_fallbacks=index == 0,
        )
        if reference is None:
            continue
        identity = (
            reference.asset_id,
            reference.source_asset_id,
            reference.display_name,
            reference.lead_party_name,
            reference.fuel_type,
            reference.generation_capacity_mw,
            reference.demand_capacity_mw,
            reference.eic,
        )
        if identity in seen:
            continue
        seen.add(identity)
        entries.append(_ReferenceEntry(stored=row, reference=reference))
    return tuple(entries)


def _rehydrate_reference(
    row: StoredAssetRead,
    values: Mapping[str, Any],
    *,
    provenance: Provenance,
    use_row_fallbacks: bool,
) -> AssetReference | None:
    national_id = _optional_text(values.get("nationalGridBmUnit"))
    if national_id is None:
        return None
    try:
        return AssetReference(
            asset_id=national_id,
            source_asset_id=_optional_text(values.get("elexonBmUnit")),
            display_name=_optional_text(values.get("bmUnitName"))
            or (_optional_text(row.display_name) if use_row_fallbacks else None),
            fuel_type=_optional_text(values.get("fuelType"))
            or (_optional_text(row.fuel_type) if use_row_fallbacks else None),
            lead_party_name=_optional_text(values.get("leadPartyName"))
            or (_optional_text(row.counterparty) if use_row_fallbacks else None),
            lead_party_id=_optional_text(values.get("leadPartyId")),
            asset_type=_optional_text(values.get("bmUnitType")),
            production_or_consumption=_optional_text(
                values.get("productionOrConsumptionFlag")
            ),
            submits_physical_notifications=_optional_bool(values.get("fpnFlag")),
            generation_capacity_mw=_nonnegative_float(
                values.get("generationCapacityMW"),
                row.capacity_mw if use_row_fallbacks else None,
            ),
            demand_capacity_mw=_finite_float(values.get("demandCapacityMW")),
            gsp_group_id=_optional_text(values.get("gspGroupId")),
            gsp_group_name=_optional_text(values.get("gspGroupName")),
            interconnector_id=_optional_text(values.get("interconnectorId")),
            eic=_optional_text(values.get("eic")),
            # Elexon reference rows never supply generator map coordinates.
            location=None,
            provenance=provenance,
            transmission_loss_factor=_finite_float(
                values.get("transmissionLossFactor")
            ),
            working_day_credit_assessment_import_capability_mw=_finite_float(
                values.get("workingDayCreditAssessmentImportCapabilityMW")
            ),
            non_working_day_credit_assessment_import_capability_mw=_finite_float(
                values.get("nonWorkingDayCreditAssessmentImportCapabilityMW")
            ),
            working_day_credit_assessment_export_capability_mw=_finite_float(
                values.get("workingDayCreditAssessmentExportCapabilityMW")
            ),
            non_working_day_credit_assessment_export_capability_mw=_finite_float(
                values.get("nonWorkingDayCreditAssessmentExportCapabilityMW")
            ),
            credit_qualifying_status=_optional_bool(
                values.get("creditQualifyingStatus")
            ),
            demand_in_production=_optional_bool(
                values.get("demandInProductionFlag")
            ),
        )
    except ValueError:
        return None


def _source_status(
    snapshot: _CatalogSnapshot,
    *,
    evaluated_at: datetime,
) -> AssetFeedStatusResponse:
    last_success = _optional_datetime(
        snapshot.catalog.latest_successes.get(REPD_SOURCE_ID)
    )
    if last_success is None:
        state = AssetFeedState.UNAVAILABLE
    else:
        lag = max(0.0, (evaluated_at - last_success).total_seconds())
        # REPD is quarterly. One publication cadence is current; a missed
        # quarter is delayed; after two cadences the register is stale. Do not
        # let a once-successful worker make year-old geography look current.
        if lag <= REPD_EXPECTED_CADENCE_SECONDS:
            state = AssetFeedState.CURRENT
        elif lag < REPD_EXPECTED_CADENCE_SECONDS * 2:
            state = AssetFeedState.DELAYED
        else:
            state = AssetFeedState.STALE
    return AssetFeedStatusResponse(
        state=state,
        last_successful_at=last_success,
        asset_reference_count=snapshot.authoritative_reference_count,
        located_asset_count=snapshot.located_site_count,
    )


def _site_source(site: REPDSite) -> AssetSourceResponse:
    return AssetSourceResponse(
        source_id=REPD_SOURCE_ID,
        publisher=REPD_PUBLISHER,
        dataset=site.provenance.dataset,
        source_record_id=site.source_id,
        retrieved_at=site.provenance.retrieved_at,
        canonical_url=REPD_PUBLICATION_URL,
        licence=REPD_LICENCE_NAME,
        attribution=REPD_SOURCE_ATTRIBUTION,
    )


def _linked_bm_row(
    snapshot: _CatalogSnapshot,
    decision: AssetLinkDecision | None,
) -> StoredAssetRead | None:
    if decision is None or not decision.is_linked or decision.elexon_asset_id is None:
        return None
    return snapshot.bm_rows_by_national_id.get(decision.elexon_asset_id)


def _evidence_by_asset(
    evidence: AssetEvidenceRead,
    *,
    evaluated_at: datetime,
) -> tuple[
    dict[UUID, tuple[PlannedSegmentRead, ...]],
    dict[UUID, tuple[SettledEnergyRead, ...]],
]:
    plans: dict[UUID, list[PlannedSegmentRead]] = defaultdict(list)
    latest_settled: dict[tuple[UUID, str, int], SettledEnergyRead] = {}
    for row in evidence.plans:
        if row.retrieved_at <= evaluated_at and row.level_at(evaluated_at) is not None:
            plans[row.asset_id].append(row)
    for row in evidence.settled:
        if row.interval_end > evaluated_at or row.retrieved_at > evaluated_at:
            continue
        key = (row.asset_id, row.settlement_date, row.settlement_period)
        existing = latest_settled.get(key)
        if existing is None or (row.revision, row.retrieved_at) > (
            existing.revision,
            existing.retrieved_at,
        ):
            latest_settled[key] = row
    settled: dict[UUID, list[SettledEnergyRead]] = defaultdict(list)
    for row in latest_settled.values():
        settled[row.asset_id].append(row)
    return (
        {
            asset_id: tuple(
                sorted(
                    rows,
                    key=lambda row: (row.retrieved_at, row.segment_start),
                    reverse=True,
                )
            )
            for asset_id, rows in plans.items()
        },
        {
            asset_id: tuple(
                sorted(
                    rows,
                    key=lambda row: (
                        row.interval_end,
                        row.revision,
                        row.retrieved_at,
                    ),
                    reverse=True,
                )
            )
            for asset_id, rows in settled.items()
        },
    )


def _present_plan(
    row: PlannedSegmentRead,
    *,
    evaluated_at: datetime,
) -> AssetPlanEvidenceResponse | None:
    level = row.level_at(evaluated_at)
    if level is None or not math.isfinite(level):
        return None
    return AssetPlanEvidenceResponse(
        level_mw=level,
        at=evaluated_at,
        direction=_direction(level),
        source_id="elexon.pn",
        retrieved_at=row.retrieved_at,
        settlement_date=row.settlement_date,
        settlement_period=row.settlement_period,
        caveat=PLAN_CAVEAT,
    )


def _present_settled(
    row: SettledEnergyRead,
) -> AssetSettledEvidenceResponse | None:
    values = (row.energy_mwh, row.average_mw)
    if not all(math.isfinite(value) for value in values):
        return None
    try:
        return AssetSettledEvidenceResponse(
            energy_mwh=row.energy_mwh,
            average_mw=row.average_mw,
            interval_start=row.interval_start,
            interval_end=row.interval_end,
            direction=_direction(row.average_mw),
            source_id="elexon.b1610",
            retrieved_at=row.retrieved_at,
            settlement_date=row.settlement_date,
            settlement_period=row.settlement_period,
            caveat=SETTLED_CAVEAT,
        )
    except ValueError:
        return None


def _selected_reference(
    entries: Sequence[_ReferenceEntry],
    decision: AssetLinkDecision,
) -> AssetReference | None:
    candidates = [
        entry.reference
        for entry in entries
        if entry.reference.asset_id == decision.elexon_asset_id
        and entry.reference.source_asset_id == decision.elexon_source_asset_id
    ]
    if not candidates:
        candidates = [
            entry.reference
            for entry in entries
            if entry.reference.asset_id == decision.elexon_asset_id
        ]
    if not candidates:
        return None

    evidence_values = {
        (item.elexon_field, item.elexon_value) for item in decision.evidence
    }

    def score(reference: AssetReference) -> tuple[int, str, str]:
        fields = {
            ("display_name", reference.display_name or ""),
            ("lead_party_name", reference.lead_party_name or ""),
            ("fuel_type", reference.fuel_type or ""),
            (
                "generation_capacity_mw",
                _number_text(reference.generation_capacity_mw),
            ),
            ("demand_capacity_mw", _number_text(reference.demand_capacity_mw)),
        }
        return (
            len(fields & evidence_values),
            reference.display_name or "",
            reference.eic or "",
        )

    return max(candidates, key=score)


def _present_bm_unit(
    reference: AssetReference,
    decision: AssetLinkDecision,
) -> BMUnitSummaryResponse:
    confidence = (
        1.0
        if decision.method is AssetLinkMethod.EXACT_SITE_IDENTITY
        else 0.95
    )
    return BMUnitSummaryResponse(
        national_grid_bm_unit=reference.asset_id,
        elexon_bm_unit=reference.source_asset_id,
        name=reference.display_name,
        fuel_type=reference.fuel_type,
        lead_party_name=reference.lead_party_name,
        generation_capacity_mw=reference.generation_capacity_mw,
        demand_capacity_mw=reference.demand_capacity_mw,
        gsp_group_name=reference.gsp_group_name,
        eic=reference.eic,
        match_method=decision.method.value,
        match_confidence=confidence,
    )


def _detail_limitations(*, linked: bool) -> list[str]:
    limitations = [
        (
            "REPD is a quarterly planning/reference register of renewable "
            "energy and storage sites; it is not a complete map of all GB "
            "conventional generation."
        ),
        (
            "The map point comes from DESNZ REPD after an OSGB36-to-WGS84 "
            "transformation; Elexon's BM-unit catalogue supplies no generator "
            "coordinates."
        ),
        (
            "50Hz does not currently provide live unit-level metered output."
        ),
    ]
    if linked:
        limitations.extend(
            [
                (
                    "The BM-unit link is exposed only because one unique "
                    "high-confidence name-and-capacity match passed the "
                    "conservative cross-site collision policy."
                ),
                (
                    "Physical Notifications are participant-submitted plans, "
                    "not actual output; B1610 is delayed settled half-hour "
                    "energy, not live instantaneous power."
                ),
                (
                    f"Settled history is bounded to the newest "
                    f"{DETAIL_SETTLED_INTERVAL_LIMIT} half-hour intervals with "
                    "only the latest published revision per interval."
                ),
            ]
        )
    else:
        limitations.append(
            "No unique high-confidence Elexon BM-unit link is available for "
            "this REPD site, so unit operating evidence is withheld."
        )
    return limitations


def _asset_lifecycle(status: REPDStatus) -> AssetLifecycle:
    return AssetLifecycle(status.value)


def _direction(value: float) -> str:
    if value > 0:
        return "export"
    if value < 0:
        return "import"
    return "idle"


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _nonnegative_float(*values: object) -> float | None:
    for value in values:
        result = _finite_float(value)
        if result is not None and result >= 0:
            return result
    return None


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        try:
            return _utc(value, "datetime")
        except ValueError:
            return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return _utc(parsed, "datetime")
    except ValueError:
        return None


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _number_text(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"
