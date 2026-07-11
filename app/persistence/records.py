from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from app.domain.enums import FactQuality
from app.sources.types import (
    DemandRecord,
    FrequencyRecord,
    GenerationRecord,
    InterconnectorFlowRecord,
    as_utc,
)


_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class SourceProfile:
    display_name: str
    documentation_url: str | None
    licence_name: str | None
    licence_url: str | None
    attribution: str | None


SOURCE_PROFILES: dict[str, SourceProfile] = {
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
}


DATASET_CADENCE_SECONDS: dict[tuple[str, str], int] = {
    ("elexon", "FREQ"): 60,
    ("elexon", "FUELINST"): 120,
    ("elexon", "INDO"): 120,
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
    profile = SOURCE_PROFILES.get(provider.lower())
    base_url = {
        "elexon": "https://data.elexon.co.uk",
        "neso": "https://api.carbonintensity.org.uk",
    }.get(provider.lower(), "https://50hz.app")
    values = source_metadata_values(
        provider=provider,
        dataset=dataset,
        request_url=base_url,
    )
    if profile is None:
        values["display_name"] = f"50Hz worker — {dataset.upper()}"
    return values


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
