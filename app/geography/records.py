"""Pure persistence values for authoritative DESNZ REPD reference sites.

The functions here do not open sessions or issue SQL.  They translate the
source contract into the existing ``SourceMetadata`` and ``Asset`` column
shapes, while keeping complete-snapshot membership explicit for a repository to
apply atomically.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.geography.repd import (
    REPD_DATASET_NAME,
    REPD_LICENCE_NAME,
    REPD_LICENCE_URL,
    REPD_PUBLICATION_URL,
    REPD_PUBLISHER,
    REPDSite,
)


REPD_SOURCE_ID = "desnz.repd"
REPD_SOURCE_PROVIDER = "desnz"
REPD_SOURCE_DATASET = "REPD"
REPD_ASSET_TYPE = "repd_site"
REPD_SOURCE_BASE_URL = "https://www.gov.uk"
REPD_EXPECTED_CADENCE_SECONDS = 91 * 24 * 60 * 60
REPD_SOURCE_ATTRIBUTION = (
    "Renewable Energy Planning Database data supplied by the Department for "
    "Energy Security and Net Zero under the Open Government Licence v3.0."
)

_PUBLIC_SITE_ID_NAMESPACE = uuid.UUID("6e9484c3-857c-58fd-b4bb-49337e9e23ad")


@dataclass(frozen=True, slots=True)
class REPDSnapshotMembership:
    """The active keys in one successfully parsed complete REPD extract."""

    source_id: str
    asset_type: str
    active_external_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.source_id != REPD_SOURCE_ID or self.asset_type != REPD_ASSET_TYPE:
            raise ValueError("REPD snapshot membership has an invalid scope")
        canonical = tuple(
            sorted({_external_id(item) for item in self.active_external_ids})
        )
        if canonical != self.active_external_ids:
            raise ValueError("REPD snapshot membership keys must be sorted and unique")

    @property
    def is_complete(self) -> bool:
        return True

    @property
    def deactivation_scope(self) -> dict[str, str]:
        return {"source_id": self.source_id, "asset_type": self.asset_type}

    def absent_external_ids(
        self,
        existing_external_ids: Iterable[str],
    ) -> tuple[str, ...]:
        """Return existing in-scope keys absent from this complete snapshot."""

        active = frozenset(self.active_external_ids)
        existing = {_external_id(value) for value in existing_external_ids}
        return tuple(sorted(existing - active))


def repd_source_metadata_values() -> dict[str, Any]:
    """Return stable values matching every writable ``SourceMetadata`` column."""

    return {
        "id": REPD_SOURCE_ID,
        "provider": REPD_SOURCE_PROVIDER,
        "dataset": REPD_SOURCE_DATASET,
        "display_name": "DESNZ — Renewable Energy Planning Database",
        "base_url": REPD_SOURCE_BASE_URL,
        "documentation_url": REPD_PUBLICATION_URL,
        "licence_name": REPD_LICENCE_NAME,
        "licence_url": REPD_LICENCE_URL,
        "attribution": REPD_SOURCE_ATTRIBUTION,
        "expected_cadence_seconds": REPD_EXPECTED_CADENCE_SECONDS,
        "active": True,
    }


def public_repd_site_id(source_record_id: str) -> str:
    """Return a stable public identifier without exposing the source key."""

    external_id = _external_id(source_record_id)
    opaque = uuid.uuid5(
        _PUBLIC_SITE_ID_NAMESPACE,
        f"{REPD_SOURCE_ID}|{external_id}",
    )
    return f"repd_{opaque.hex}"


def normalized_repd_fuel(site: REPDSite) -> str | None:
    """Map detailed REPD technology to the app's broad display vocabulary."""

    if site.is_storage:
        return "storage"
    technology = _words(site.technology)
    if not technology or technology == "unknown":
        return None
    if "wind" in technology.split():
        return "wind"
    if technology == "solar photovoltaics" or "solar" in technology.split():
        return "solar"
    if technology in {
        "anaerobic digestion",
        "biomass co firing",
        "biomass dedicated",
        "landfill gas",
        "sewage sludge digestion",
    }:
        return "biomass"
    if "hydro" in technology.split() or technology == "hydroelectricity":
        return "hydro"
    return "other"


def map_repd_site(
    site: REPDSite,
    *,
    source_id: str = REPD_SOURCE_ID,
) -> dict[str, Any]:
    """Translate one active REPD site into the existing ``Asset`` value shape."""

    if source_id != REPD_SOURCE_ID:
        raise ValueError("REPD assets must use the canonical DESNZ source ID")
    external_id = _authoritative_external_id(site)
    if site.capacity_mw is not None and (
        not math.isfinite(site.capacity_mw) or site.capacity_mw < 0
    ):
        raise ValueError("REPD capacity must be finite and non-negative")

    coordinates = site.coordinates
    coordinate_attributes: dict[str, Any] | None = None
    if coordinates is not None:
        coordinate_attributes = {
            "eastingM": coordinates.easting_m,
            "northingM": coordinates.northing_m,
            "latitude": coordinates.latitude,
            "longitude": coordinates.longitude,
            "sourceFields": list(coordinates.source_fields),
            "sourceCRS": coordinates.source_crs,
            "outputCRS": coordinates.output_crs,
            "transform": coordinates.transform,
        }

    broad_fuel = normalized_repd_fuel(site)
    attributes: dict[str, Any] = {
        "classification": "reference",
        "snapshotKind": "complete_reference",
        "activeSemantics": "present_in_latest_complete_repd_snapshot",
        "publicId": public_repd_site_id(external_id),
        "sourceRecordId": external_id,
        "projectName": site.project_name,
        "operatorName": site.operator_name,
        "technology": site.technology,
        "normalizedFuel": broad_fuel,
        "fuelTypeDerivation": "broad_ui_mapping_from_repd_technology",
        "capacityMW": site.capacity_mw,
        "lifecycleStatus": site.status.value,
        "sourceLifecycleStatus": site.source_status,
        "storageType": site.storage_type,
        "isStorage": site.is_storage,
        "region": site.region,
        "country": site.country,
        "planningAuthority": site.planning_authority,
        "recordLastUpdated": site.record_last_updated,
        "locationStatus": (
            "source_coordinate_transformed"
            if coordinates is not None
            else "not_available_from_source"
        ),
        "coordinates": coordinate_attributes,
        "provenance": {
            "publisher": site.provenance.publisher,
            "dataset": site.provenance.dataset,
            "sourceUrl": site.provenance.source_url,
            "licenceName": site.provenance.licence_name,
            "licenceUrl": site.provenance.licence_url,
            "retrievedAt": site.provenance.retrieved_at.isoformat(),
        },
    }

    return {
        "source_id": source_id,
        "external_id": external_id,
        "asset_type": REPD_ASSET_TYPE,
        "display_name": site.project_name[:160],
        "fuel_type": broad_fuel,
        "region_code": site.region[:64] if site.region else None,
        "counterparty": site.operator_name[:120] if site.operator_name else None,
        "capacity_mw": site.capacity_mw,
        # REPD's transformed source point is authoritative enough for a map pin.
        # Normalized map_x/map_y are a separate product projection and are never
        # fabricated here.
        "latitude": coordinates.latitude if coordinates is not None else None,
        "longitude": coordinates.longitude if coordinates is not None else None,
        "map_x": None,
        "map_y": None,
        "active": True,
        "attributes": attributes,
    }


def repd_snapshot_membership(
    sites: Iterable[REPDSite],
    *,
    source_id: str = REPD_SOURCE_ID,
) -> REPDSnapshotMembership:
    """Describe authoritative active membership for a complete REPD extract."""

    if source_id != REPD_SOURCE_ID:
        raise ValueError("REPD snapshots must use the canonical DESNZ source ID")
    external_ids = tuple(sorted({_authoritative_external_id(site) for site in sites}))
    return REPDSnapshotMembership(
        source_id=source_id,
        asset_type=REPD_ASSET_TYPE,
        active_external_ids=external_ids,
    )


def _external_id(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("REPD source record ID must be a string")
    external_id = value.strip()
    if not external_id:
        raise ValueError("REPD source record ID cannot be empty")
    return external_id


def _authoritative_external_id(site: REPDSite) -> str:
    if site.provenance.publisher != REPD_PUBLISHER:
        raise ValueError("REPD site publisher provenance is not DESNZ")
    if site.provenance.dataset != REPD_DATASET_NAME:
        raise ValueError("REPD site dataset provenance is not REPD")
    external_id = _external_id(site.source_id)
    if len(external_id) > 120:
        raise ValueError("REPD source record ID exceeds Asset.external_id capacity")
    return external_id


def _words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
