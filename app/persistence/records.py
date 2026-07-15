from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from app.assets.models import (
    AssetReference,
    EvidenceKind,
    PlannedProfileSegment,
    SettledMeteredEnergy,
)
from app.domain.enums import FactQuality
from app.geography.records import (
    REPD_EXPECTED_CADENCE_SECONDS,
    REPD_SOURCE_ATTRIBUTION,
)
from app.geography.repd import (
    REPD_LICENCE_NAME,
    REPD_LICENCE_URL,
    REPD_PUBLICATION_URL,
)
from app.sources.types import (
    CarbonIntensityRecord,
    DataClassification as SourceDataClassification,
    DemandForecastRecord,
    DemandRecord,
    DistributionIncidentRecord,
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    RemitUnavailabilityRecord,
    SystemWarningRecord,
    WindForecastRecord,
    as_utc,
)
from app.sources.ukpn import contains_full_postcode


_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class SourceProfile:
    display_name: str
    documentation_url: str | None
    licence_name: str | None
    licence_url: str | None
    attribution: str | None


SOURCE_PROFILES: dict[str, SourceProfile] = {
    "desnz": SourceProfile(
        display_name="DESNZ Renewable Energy Planning Database",
        documentation_url=REPD_PUBLICATION_URL,
        licence_name=REPD_LICENCE_NAME,
        licence_url=REPD_LICENCE_URL,
        attribution=REPD_SOURCE_ATTRIBUTION,
    ),
    "elexon": SourceProfile(
        display_name="Elexon Insights",
        documentation_url="https://bmrs.elexon.co.uk/api-documentation",
        licence_name="Elexon data terms",
        licence_url="https://www.elexon.co.uk/about/copyright/",
        attribution="Data supplied by Elexon Limited.",
    ),
    "neso": SourceProfile(
        display_name="NESO Carbon Intensity",
        documentation_url="https://carbonintensity.org.uk/",
        licence_name=None,
        licence_url=None,
        attribution="Carbon-intensity data supplied by NESO.",
    ),
    "ukpn": SourceProfile(
        display_name="UK Power Networks",
        documentation_url=(
            "https://ukpowernetworks.opendatasoft.com/explore/dataset/"
            "ukpn-live-faults/"
        ),
        licence_name="CC BY 4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Live Faults data supplied by UK Power Networks.",
    ),
}

# Public source inspection is an explicit product contract. Operational
# placeholder metadata (for example one failed history chunk) must never become
# a new user-facing publisher merely because it has a source_metadata row.
PUBLIC_SOURCE_PROVIDERS = tuple(sorted(SOURCE_PROFILES))
PUBLIC_SOURCE_IDS = (
    "desnz.repd",
    "elexon.b1610",
    "elexon.bm-unit-reference",
    "elexon.freq",
    "elexon.fuelinst",
    "elexon.indo",
    "elexon.ndf",
    "elexon.pn",
    "elexon.remit",
    "elexon.syswarn",
    "elexon.windfor",
    "neso.carbon-intensity-national",
    "neso.carbon-intensity-regional",
    "ukpn.live-faults",
)


DATASET_CADENCE_SECONDS: dict[tuple[str, str], int] = {
    # REPD is a quarterly reference register. The worker checks GOV.UK daily
    # for a changed content-addressed attachment, but the dataset itself must
    # never be presented as a daily or live operating feed.
    ("desnz", "REPD"): REPD_EXPECTED_CADENCE_SECONDS,
    ("elexon", "B1610"): 86_400,
    ("elexon", "BM_UNIT_REFERENCE"): 86_400,
    ("elexon", "FREQ"): 60,
    # FUELINST observations are five-minute facts. The worker polls every two
    # minutes for low pickup latency, but freshness must follow publication
    # cadence rather than our polling interval.
    ("elexon", "FUELINST"): 300,
    # INDO represents half-hour settlement periods and is published after the
    # period.  The worker polls more often for low detection latency, but that
    # polling interval is not the data's actual cadence.
    ("elexon", "INDO"): 1800,
    ("elexon", "NDF"): 1800,
    ("elexon", "PN"): 600,
    ("elexon", "WINDFOR"): 1800,
    ("elexon", "REMIT"): 300,
    ("elexon", "SYSWARN"): 300,
    ("neso", "CARBON_INTENSITY"): 1800,
    ("neso", "CARBON_INTENSITY_NATIONAL"): 1800,
    ("neso", "CARBON_INTENSITY_REGIONAL"): 1800,
    ("ukpn", "LIVE_FAULTS"): 300,
}


INTERCONNECTOR_COUNTERPARTIES: dict[str, str] = {
    "INTELEC": "France",
    "INTEW": "Ireland",
    "INTFR": "France",
    "INTGRNL": "Ireland",
    "INTIFA2": "France",
    "INTIRL": "Northern Ireland",
    "INTNED": "Netherlands",
    "INTNEM": "Belgium",
    "INTNSL": "Norway",
    "INTVKL": "Denmark",
}


BM_UNIT_REFERENCE_SOURCE_ID = "elexon.bm-unit-reference"
BM_UNIT_REFERENCE_REQUEST_URL = (
    "https://data.elexon.co.uk/bmrs/api/v1/reference/bmunits/all"
)


def canonical_source_id(provider: str, dataset: str) -> str:
    """Return a stable dataset-level FK that fits the database's 64-char key."""

    provider_slug = _slug(provider)
    dataset_slug = _slug(dataset)
    candidate = f"{provider_slug}.{dataset_slug}"
    if len(candidate) <= 64:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:12]
    return f"{candidate[:51]}.{digest}"


def source_metadata_values(
    *,
    provider: str,
    dataset: str,
    request_url: str,
    expected_cadence_seconds: int | None = None,
) -> dict[str, Any]:
    provider_key = provider.strip().lower()
    if provider_key not in SOURCE_PROFILES:
        provider_root = re.split(r"[.:/]", provider_key, maxsplit=1)[0]
        if provider_root in SOURCE_PROFILES:
            provider_key = provider_root
    dataset_key = dataset.strip().upper()
    profile = SOURCE_PROFILES.get(provider_key)
    parsed = urlsplit(request_url)
    base_url = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme and parsed.netloc
        else request_url
    )
    display_name = profile.display_name if profile else provider.strip().title()
    cadence = expected_cadence_seconds or DATASET_CADENCE_SECONDS.get(
        (provider_key, dataset_key), 300
    )
    if cadence <= 0:
        raise ValueError("expected source cadence must be positive")
    return {
        "id": canonical_source_id(provider_key, dataset_key),
        "provider": provider_key,
        "dataset": dataset_key,
        "display_name": f"{display_name} — {dataset_key}",
        "base_url": base_url,
        "documentation_url": profile.documentation_url if profile else None,
        "licence_name": profile.licence_name if profile else None,
        "licence_url": profile.licence_url if profile else None,
        "attribution": profile.attribution if profile else None,
        "expected_cadence_seconds": cadence,
        "active": True,
    }


def job_source_metadata_values(job_id: str) -> dict[str, Any]:
    """Create a safe FK target even when the first source request fails."""

    base_job = job_id.removesuffix(".reconcile")
    provider, separator, dataset = base_job.partition(".")
    if not separator:
        provider, dataset = "worker", base_job
    base_url = {
        "desnz": "https://www.gov.uk",
        "elexon": "https://data.elexon.co.uk",
        "neso": "https://api.carbonintensity.org.uk",
        "ukpn": "https://ukpowernetworks.opendatasoft.com",
    }.get(provider.lower(), "https://50hz.app")
    values = source_metadata_values(
        provider=provider,
        dataset=dataset,
        request_url=base_url,
    )
    if values["id"] not in PUBLIC_SOURCE_IDS:
        values["display_name"] = f"50Hz worker — {dataset.upper()}"
        values["active"] = False
    return values


def bm_unit_reference_source_metadata_values() -> dict[str, Any]:
    """Stable FK metadata used by both authoritative and placeholder BM assets."""

    return source_metadata_values(
        provider="elexon.bm-unit-reference",
        dataset="BM_UNIT_REFERENCE",
        request_url=BM_UNIT_REFERENCE_REQUEST_URL,
    )


def map_asset_reference(record: AssetReference, *, source_id: str) -> dict[str, Any]:
    if record.provenance.evidence_kind is not EvidenceKind.REFERENCE:
        raise ValueError("BM-unit asset rows require reference evidence")
    if record.generation_capacity_mw is not None and record.generation_capacity_mw < 0:
        raise ValueError("generation capacity cannot be negative")
    display_name = record.display_name or record.source_asset_id or record.asset_id
    return {
        "source_id": source_id,
        "external_id": record.asset_id,
        "asset_type": "bm_unit",
        "display_name": display_name[:160],
        "fuel_type": record.fuel_type.lower() if record.fuel_type else None,
        "region_code": record.gsp_group_id,
        "counterparty": (
            record.lead_party_name[:120] if record.lead_party_name else None
        ),
        "capacity_mw": record.generation_capacity_mw,
        # Elexon's BM-unit reference catalogue has no authoritative point
        # coordinates. GSP groups and names must never be converted into pins.
        "latitude": None,
        "longitude": None,
        "map_x": None,
        "map_y": None,
        "active": True,
        "attributes": {
            "classification": EvidenceKind.REFERENCE.value,
            "nationalGridBmUnit": record.asset_id,
            "elexonBmUnit": record.source_asset_id,
            "bmUnitName": record.display_name,
            "fuelType": record.fuel_type,
            "leadPartyName": record.lead_party_name,
            "leadPartyId": record.lead_party_id,
            "bmUnitType": record.asset_type,
            "productionOrConsumptionFlag": record.production_or_consumption,
            "fpnFlag": record.submits_physical_notifications,
            "generationCapacityMW": record.generation_capacity_mw,
            "demandCapacityMW": record.demand_capacity_mw,
            "transmissionLossFactor": record.transmission_loss_factor,
            "workingDayCreditAssessmentImportCapabilityMW": (
                record.working_day_credit_assessment_import_capability_mw
            ),
            "nonWorkingDayCreditAssessmentImportCapabilityMW": (
                record.non_working_day_credit_assessment_import_capability_mw
            ),
            "workingDayCreditAssessmentExportCapabilityMW": (
                record.working_day_credit_assessment_export_capability_mw
            ),
            "nonWorkingDayCreditAssessmentExportCapabilityMW": (
                record.non_working_day_credit_assessment_export_capability_mw
            ),
            "creditQualifyingStatus": record.credit_qualifying_status,
            "demandInProductionFlag": record.demand_in_production,
            "gspGroupId": record.gsp_group_id,
            "gspGroupName": record.gsp_group_name,
            "interconnectorId": record.interconnector_id,
            "eic": record.eic,
            "locationStatus": "not_provided_by_elexon",
            "provenance": _asset_provenance(record),
        },
    }


def map_physical_notification_segment(
    record: PlannedProfileSegment,
    *,
    source_id: str,
    raw_payload_id: UUID,
    asset_id: UUID,
) -> dict[str, Any]:
    if record.provenance.evidence_kind is not EvidenceKind.REPORTED_PLAN:
        raise ValueError("PN segments must remain reported plans")
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "asset_id": asset_id,
        "national_grid_bm_unit": record.asset_id,
        "elexon_bm_unit": record.source_asset_id,
        "settlement_date": record.settlement_date,
        "settlement_period": record.settlement_period,
        "segment_start": record.start,
        "segment_end": record.end,
        "level_from_mw": float(record.level_from_mw),
        "level_to_mw": float(record.level_to_mw),
        "classification": EvidenceKind.REPORTED_PLAN.value,
        "retrieved_at": record.provenance.retrieved_at,
        "attributes": {
            "semantics": "participant_submitted_planned_profile",
            "isActualOutput": False,
            "signConvention": "positive_export_negative_import",
            "provenance": _asset_provenance(record),
        },
    }


def map_b1610_settled_energy(
    record: SettledMeteredEnergy,
    *,
    source_id: str,
    raw_payload_id: UUID,
    asset_id: UUID,
) -> dict[str, Any]:
    if record.provenance.evidence_kind is not EvidenceKind.SETTLED_METERED:
        raise ValueError("B1610 rows must remain settled-metered evidence")
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "asset_id": asset_id,
        "national_grid_bm_unit": record.national_grid_bm_unit,
        "elexon_bm_unit": record.source_asset_id,
        "settlement_date": record.settlement_date,
        "settlement_period": record.settlement_period,
        "interval_start": record.interval_start,
        "interval_end": record.interval_end,
        "energy_mwh": float(record.energy_mwh),
        "average_mw": float(record.average_mw),
        "psr_type": record.psr_type,
        "classification": EvidenceKind.SETTLED_METERED.value,
        "retrieved_at": record.provenance.retrieved_at,
        "revision": 0,
        "attributes": {
            "semantics": "delayed_settled_half_hour_energy",
            "assetExternalID": record.asset_id,
            "nationalGridBmUnit": record.national_grid_bm_unit,
            "elexonBmUnit": record.source_asset_id,
            "isInstantaneousPower": False,
            "averageMWMethod": "energy_mwh_divided_by_0.5_hours",
            "signConvention": "positive_export_negative_import",
            "provenance": _asset_provenance(record),
        },
    }


def map_generation_record(
    record: GenerationRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    return {
        **_common_observation_values(record, source_id, raw_payload_id),
        "series_key": record.fuel_code.upper(),
        "fuel_type": record.fuel_type,
        "asset_id": None,
        "generation_mw": float(record.generation_mw),
        "settlement_date": record.settlement_date,
        "settlement_period": record.settlement_period,
        "attributes": {
            "source": record.source,
            "dataset": record.dataset,
            "fuelCode": record.fuel_code,
        },
    }


def map_demand_record(
    record: DemandRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    return {
        **_common_observation_values(record, source_id, raw_payload_id),
        "series_key": "gb",
        "demand_type": record.dataset.lower(),
        "demand_mw": float(record.demand_mw),
        "settlement_date": record.settlement_date,
        "settlement_period": record.settlement_period,
        "attributes": {"source": record.source, "dataset": record.dataset},
    }


def map_frequency_record(
    record: FrequencyRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    return {
        **_common_observation_values(record, source_id, raw_payload_id),
        "series_key": "gb",
        "frequency_hz": float(record.frequency_hz),
        "attributes": {"source": record.source, "dataset": record.dataset},
    }


def map_interconnector_record(
    record: InterconnectorFlowRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    connector_code = record.interconnector_id.upper()
    return {
        **_common_observation_values(record, source_id, raw_payload_id),
        "connector_code": connector_code,
        "asset_id": None,
        "counterparty": INTERCONNECTOR_COUNTERPARTIES.get(
            connector_code, record.interconnector_name
        ),
        "flow_mw": float(record.flow_mw),
        "attributes": {
            "source": record.source,
            "dataset": record.dataset,
            "displayName": record.interconnector_name,
            "signConvention": "positive_import_into_gb",
        },
    }


def map_distribution_incident_record(
    record: DistributionIncidentRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    if contains_full_postcode(asdict(record)):
        raise ValueError("distribution incident record contains a full postcode")
    if record.classification is not SourceDataClassification.REPORTED:
        raise ValueError("distribution incidents must be source-reported facts")
    if record.status not in {"planned", "unplanned", "restored"}:
        raise ValueError("distribution incident status is unsupported")
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "incident_reference": record.incident_reference,
        "revision": 0,
        "content_sha256": record.content_sha256,
        "classification": record.classification.value,
        "status": record.status,
        "status_id": record.status_id,
        "source_created_at": (
            as_utc(record.source_created_at, field_name="source_created_at")
            if record.source_created_at is not None
            else None
        ),
        "observed_at": as_utc(record.observed_at, field_name="observed_at"),
        "retrieved_at": as_utc(record.retrieved_at, field_name="retrieved_at"),
        "incident_start": (
            as_utc(record.incident_start, field_name="incident_start")
            if record.incident_start is not None
            else None
        ),
        "restored_at": (
            as_utc(record.restored_at, field_name="restored_at")
            if record.restored_at is not None
            else None
        ),
        "estimated_restoration_at": (
            as_utc(
                record.estimated_restoration_at,
                field_name="estimated_restoration_at",
            )
            if record.estimated_restoration_at is not None
            else None
        ),
        "customers_affected": record.customers_affected,
        "calls_reported": record.calls_reported,
        "postcode_sectors": list(record.postcode_sectors),
        "outward_codes": list(record.outward_codes),
        "latitude": record.latitude,
        "longitude": record.longitude,
        "geography_precision": record.geography_precision,
        "operating_zone": record.operating_zone,
        "official_summary": record.official_summary,
        "official_details": record.official_details,
        "restoration_window_text": record.restoration_window_text,
        "incident_category": record.incident_category,
    }


def carbon_region_code(record: CarbonIntensityRecord) -> str:
    if record.postcode:
        return record.postcode.upper()
    if record.region_id is not None:
        return f"region-{record.region_id}"
    return "GB"


def map_carbon_actual_record(
    record: CarbonIntensityRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    if record.classification is not SourceDataClassification.ESTIMATED:
        raise ValueError("only estimated carbon actuals belong in carbon_observations")
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "source_record_id": record.source_key,
        "observed_at": as_utc(record.period_start, field_name="period_start"),
        "published_at": None,
        "retrieved_at": as_utc(record.retrieved_at, field_name="retrieved_at"),
        "revision": 0,
        "quality": FactQuality.ESTIMATED,
        "region_code": carbon_region_code(record),
        "intensity_gco2_kwh": float(record.intensity_g_co2_per_kwh),
        "index_label": record.index,
        "generation_mix": [
            {"fuel": share.fuel_type, "percent": float(share.percent)}
            for share in record.generation_mix
        ],
        "attributes": _carbon_attributes(record),
    }


def map_carbon_forecast_record(
    record: CarbonIntensityRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    if record.classification is not SourceDataClassification.FORECAST:
        raise ValueError("only forecast carbon records belong in forecast_observations")
    retrieved_at = as_utc(record.retrieved_at, field_name="retrieved_at")
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "source_record_id": record.source_key,
        "metric_type": "carbon_intensity",
        "series_key": carbon_region_code(record),
        "variant": "point",
        "value": float(record.intensity_g_co2_per_kwh),
        "unit": "gCO2/kWh",
        "value_low": None,
        "value_high": None,
        "valid_from": as_utc(record.period_start, field_name="period_start"),
        "valid_to": as_utc(record.period_end, field_name="period_end"),
        # NESO's payload does not publish an issue timestamp. Retrieval time is
        # retained explicitly as the version boundary rather than inventing one.
        "issued_at": retrieved_at,
        "published_at": None,
        "retrieved_at": retrieved_at,
        "revision": 0,
        "model_name": "neso_carbon_intensity",
        "settlement_date": None,
        "settlement_period": None,
        "attributes": {
            **_carbon_attributes(record),
            "classification": "forecast",
            "issueTimeBasis": "retrieved_at",
        },
    }


def map_demand_forecast_record(
    record: DemandForecastRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "source_record_id": record.source_key,
        "metric_type": "demand",
        "series_key": (record.boundary or "gb").strip().lower(),
        "variant": "point",
        "value": float(record.demand_mw),
        "unit": "MW",
        "value_low": None,
        "value_high": None,
        "valid_from": as_utc(record.forecast_for, field_name="forecast_for"),
        "valid_to": None,
        "issued_at": as_utc(record.published_at, field_name="published_at"),
        "published_at": as_utc(record.published_at, field_name="published_at"),
        "retrieved_at": as_utc(record.retrieved_at, field_name="retrieved_at"),
        "revision": 0,
        "model_name": record.dataset,
        "settlement_date": record.settlement_date,
        "settlement_period": record.settlement_period,
        "attributes": {
            "source": record.source,
            "dataset": record.dataset,
            "classification": "forecast",
            "boundary": record.boundary,
        },
    }


def map_wind_forecast_record(
    record: WindForecastRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "source_record_id": record.source_key,
        "metric_type": "generation",
        "series_key": "wind",
        "variant": "point",
        "value": float(record.generation_mw),
        "unit": "MW",
        "value_low": None,
        "value_high": None,
        "valid_from": as_utc(record.forecast_for, field_name="forecast_for"),
        "valid_to": None,
        "issued_at": as_utc(record.published_at, field_name="published_at"),
        "published_at": as_utc(record.published_at, field_name="published_at"),
        "retrieved_at": as_utc(record.retrieved_at, field_name="retrieved_at"),
        "revision": 0,
        "model_name": record.dataset,
        "settlement_date": None,
        "settlement_period": None,
        "attributes": {
            "source": record.source,
            "dataset": record.dataset,
            "classification": "forecast",
            "fuelType": "wind",
        },
    }


def map_remit_notice_record(
    record: RemitUnavailabilityRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    outage_profile = [
        {
            "start": as_utc(point.start, field_name="outage_profile.start").isoformat(),
            "end": as_utc(point.end, field_name="outage_profile.end").isoformat(),
            "availableCapacityMW": float(point.available_capacity_mw),
        }
        for point in record.outage_profile
    ]
    evidence = {
        "classification": "reported",
        "messageId": record.message_id,
        "mRID": record.mrid,
        "revisionNumber": record.revision_number,
        "outageProfile": outage_profile,
    }
    source_content = asdict(record)
    # Retrieval time is local delivery metadata, not part of the source's
    # revision.  Including it would manufacture a new evidence checksum on
    # every poll even when Elexon returned the same REMIT revision unchanged.
    source_content.pop("retrieved_at", None)
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "notice_kind": "remit_unavailability",
        "external_id": record.mrid,
        "revision_key": f"r{record.revision_number}",
        "revision_number": record.revision_number,
        "source_record_id": record.source_key,
        "content_sha256": _content_checksum(source_content),
        "classification": "reported",
        "published_at": as_utc(record.published_at, field_name="published_at"),
        "source_created_at": as_utc(record.created_at, field_name="created_at"),
        "retrieved_at": as_utc(record.retrieved_at, field_name="retrieved_at"),
        "event_start": as_utc(record.event_start, field_name="event_start"),
        "event_end": (
            as_utc(record.event_end, field_name="event_end")
            if record.event_end is not None
            else None
        ),
        "heading": record.message_heading,
        "event_type": record.event_type,
        "unavailability_type": record.unavailability_type,
        "event_status": record.event_status,
        "participant_id": record.participant_id,
        "asset_id": record.asset_id,
        "asset_type": record.asset_type,
        "affected_unit": record.affected_unit,
        "affected_unit_eic": record.affected_unit_eic,
        "affected_area": record.affected_area,
        "bidding_zone": record.bidding_zone,
        "fuel_type": record.fuel_type,
        "normal_capacity_mw": record.normal_capacity_mw,
        "available_capacity_mw": record.available_capacity_mw,
        "unavailable_capacity_mw": record.unavailable_capacity_mw,
        "duration_uncertainty": record.duration_uncertainty,
        "reported_cause": record.reported_cause,
        "reported_related_information": record.reported_related_information,
        "warning_type": None,
        "warning_text": None,
        "evidence": evidence,
    }


def map_system_warning_record(
    record: SystemWarningRecord,
    *,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    published_at = as_utc(record.published_at, field_name="published_at")
    identity = f"{record.warning_type}\0{published_at.isoformat()}"
    external_id = f"syswarn:{hashlib.sha256(identity.encode()).hexdigest()[:32]}"
    revision_key = (
        f"r{record.revision_number}"
        if record.revision_number is not None
        else record.content_sha256.lower()
    )
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "notice_kind": "system_warning",
        "external_id": external_id,
        "revision_key": revision_key,
        "revision_number": record.revision_number,
        "source_record_id": record.source_key,
        "content_sha256": record.content_sha256.lower(),
        "classification": "reported",
        "published_at": published_at,
        "source_created_at": None,
        "retrieved_at": as_utc(record.retrieved_at, field_name="retrieved_at"),
        "event_start": None,
        "event_end": None,
        "heading": None,
        "event_type": None,
        "unavailability_type": None,
        "event_status": None,
        "participant_id": None,
        "asset_id": None,
        "asset_type": None,
        "affected_unit": None,
        "affected_unit_eic": None,
        "affected_area": None,
        "bidding_zone": None,
        "fuel_type": None,
        "normal_capacity_mw": None,
        "available_capacity_mw": None,
        "unavailable_capacity_mw": None,
        "duration_uncertainty": None,
        "reported_cause": None,
        "reported_related_information": None,
        "warning_type": record.warning_type,
        "warning_text": record.warning_text,
        "evidence": {
            "classification": "reported",
            "warningType": record.warning_type,
            "warningTextSha256": record.content_sha256.lower(),
        },
    }


def _carbon_attributes(record: CarbonIntensityRecord) -> dict[str, Any]:
    return {
        "source": record.source,
        "dataset": record.dataset,
        "classification": record.classification.value,
        "periodEnd": as_utc(record.period_end, field_name="period_end").isoformat(),
        "regionId": record.region_id,
        "regionName": record.region_name,
        "dnoRegion": record.dno_region,
        "postcode": record.postcode,
    }


def _asset_provenance(
    record: AssetReference | PlannedProfileSegment | SettledMeteredEnergy,
) -> dict[str, Any]:
    provenance = record.provenance
    return {
        "sourceId": provenance.source_id,
        "dataset": provenance.dataset,
        "endpoint": provenance.endpoint,
        "retrievedAt": provenance.retrieved_at.isoformat(),
        "publishedAt": (
            provenance.published_at.isoformat()
            if provenance.published_at is not None
            else None
        ),
        "evidenceKind": provenance.evidence_kind.value,
    }


def _content_checksum(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _common_observation_values(
    record: GenerationRecord | DemandRecord | FrequencyRecord | InterconnectorFlowRecord,
    source_id: str,
    raw_payload_id: UUID,
) -> dict[str, Any]:
    published_at: datetime | None = record.published_at
    return {
        "source_id": source_id,
        "raw_payload_id": raw_payload_id,
        "source_record_id": record.source_key,
        "observed_at": as_utc(record.observed_at, field_name="observed_at"),
        "published_at": (
            as_utc(published_at, field_name="published_at")
            if published_at is not None
            else None
        ),
        "retrieved_at": as_utc(record.retrieved_at, field_name="retrieved_at"),
        "revision": 0,
        "quality": FactQuality.VALIDATED,
    }


def _slug(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("source provider and dataset cannot be blank")
    return slug
