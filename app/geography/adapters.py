"""Economical collector for DESNZ's official quarterly REPD publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.geography.repd import REPDSchemaError, REPDSite, parse_repd_csv
from app.sources.client import AsyncJSONClient
from app.sources.types import AdapterResult, ObservationWindow


GOV_UK_BASE_URL = "https://www.gov.uk/"
REPD_CONTENT_ENDPOINT = (
    "api/content/government/publications/"
    "renewable-energy-planning-database-quarterly-extract"
)
REPD_ASSET_HOST = "assets.publishing.service.gov.uk"


@dataclass(frozen=True, slots=True)
class REPDPublicationAttachment:
    attachment_id: str
    title: str
    filename: str
    url: str
    content_type: str
    file_size: int | None
    public_updated_at: str | None


class REPDReferenceAdapter:
    """Discover and fetch the current REPD CSV without hard-coding its hash.

    GOV.UK asset URLs are content-addressed. The small Content API document is
    checked daily; an unchanged attachment is served from this worker's parsed
    in-memory snapshot, so the roughly 5 MB CSV is normally downloaded only at
    process start and when DESNZ publishes a new quarterly extract.
    """

    source_id = "desnz.repd"
    dataset = "REPD"
    endpoint = REPD_CONTENT_ENDPOINT

    def __init__(
        self,
        client: AsyncJSONClient,
        *,
        max_bytes: int = 20_000_000,
        max_records: int = 25_000,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.client = client
        self.max_bytes = max_bytes
        self.max_records = max_records
        self._cached_attachment_url: str | None = None
        self._cached_result: AdapterResult[REPDSite] | None = None

    async def fetch(
        self,
        window: ObservationWindow,
    ) -> AdapterResult[REPDSite]:
        publication = await self.client.get_json(self.endpoint)
        attachment = select_repd_csv_attachment(
            publication.payload,
            max_bytes=self.max_bytes,
        )

        if (
            self._cached_result is not None
            and self._cached_attachment_url == attachment.url
        ):
            metadata = dict(self._cached_result.metadata)
            metadata.update(
                {
                    "cacheHit": True,
                    "publicationCheckedAt": publication.retrieved_at.isoformat(),
                    "publicationPublicUpdatedAt": attachment.public_updated_at,
                }
            )
            return replace(
                self._cached_result,
                window=window,
                metadata=metadata,
            )

        response = await self.client.get_bytes(
            attachment.url,
            headers={"Accept": "text/csv, text/plain;q=0.8"},
            max_bytes=self.max_bytes,
        )
        parsed = parse_repd_csv(
            response.raw_body,
            retrieved_at=response.retrieved_at,
            source_url=attachment.url,
        )
        if not parsed.sites:
            raise REPDSchemaError("current REPD publication has no active sites")
        if len(parsed.sites) > self.max_records:
            raise REPDSchemaError("current REPD publication exceeds safety limit")

        raw_payload = {
            "publication": {
                "contentApiURL": publication.request_url,
                "publicUpdatedAt": attachment.public_updated_at,
            },
            "attachment": {
                "id": attachment.attachment_id,
                "title": attachment.title,
                "filename": attachment.filename,
                "url": attachment.url,
                "contentType": attachment.content_type,
                "declaredFileSize": attachment.file_size,
                "etag": response.etag,
            },
            "parse": {
                "encoding": parsed.encoding,
                "inputRows": parsed.input_rows,
                "retainedRows": parsed.retained_rows,
                "excludedStatusRows": parsed.excluded_status_rows,
                "invalidRows": parsed.invalid_rows,
                "duplicateRows": parsed.duplicate_rows,
                "missingCapacityRows": parsed.missing_capacity_rows,
                "invalidCoordinateRows": parsed.invalid_coordinate_rows,
            },
        }
        result = AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=response.retrieved_at,
            request_url=response.request_url,
            records=parsed.sites,
            raw_payload=raw_payload,
            raw_body=response.raw_body,
            checksum_sha256=response.checksum_sha256,
            content_type=response.content_type,
            metadata={
                "snapshotKind": "complete_reference",
                "cacheHit": False,
                "publicationCheckedAt": publication.retrieved_at.isoformat(),
                "publicationPublicUpdatedAt": attachment.public_updated_at,
                "attachmentURL": attachment.url,
                "attachmentID": attachment.attachment_id,
                "recordCount": parsed.retained_rows,
                "locatedRecordCount": sum(
                    site.coordinates is not None for site in parsed.sites
                ),
                "referenceSemantics": "quarterly_site_register_not_live_output",
            },
            warnings=parsed.warnings,
        )
        self._cached_attachment_url = attachment.url
        self._cached_result = result
        return result


def select_repd_csv_attachment(
    payload: Any,
    *,
    max_bytes: int,
) -> REPDPublicationAttachment:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not isinstance(payload, Mapping):
        raise REPDSchemaError("GOV.UK publication response must be an object")
    details = payload.get("details")
    if not isinstance(details, Mapping):
        raise REPDSchemaError("GOV.UK publication response has no details object")
    attachments = details.get("attachments")
    if not isinstance(attachments, list):
        raise REPDSchemaError("GOV.UK publication response has no attachments")

    featured = details.get("featured_attachments")
    featured_ids = (
        [str(item) for item in featured]
        if isinstance(featured, list)
        else []
    )
    featured_rank = {attachment_id: index for index, attachment_id in enumerate(featured_ids)}
    candidates: list[tuple[int, int, Mapping[str, Any]]] = []
    for index, candidate in enumerate(attachments):
        if not isinstance(candidate, Mapping):
            continue
        filename = _optional_text(candidate.get("filename"))
        content_type = _optional_text(candidate.get("content_type"))
        if filename is None or not filename.casefold().endswith(".csv"):
            continue
        if content_type is not None and "csv" not in content_type.casefold():
            continue
        if candidate.get("accessible") is False:
            continue
        attachment_id = _optional_text(candidate.get("id")) or ""
        candidates.append(
            (featured_rank.get(attachment_id, len(featured_rank) + 1), index, candidate)
        )

    if not candidates:
        raise REPDSchemaError("GOV.UK publication has no accessible CSV attachment")
    selected = min(candidates, key=lambda item: (item[0], item[1]))[2]
    url = _required_text(selected.get("url"), "attachment URL")
    parsed_url = urlsplit(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != REPD_ASSET_HOST:
        raise REPDSchemaError("REPD attachment URL is not on the official asset host")

    file_size = _optional_positive_integer(selected.get("file_size"))
    if file_size is not None and file_size > max_bytes:
        raise REPDSchemaError("declared REPD attachment exceeds safety limit")
    return REPDPublicationAttachment(
        attachment_id=_required_text(selected.get("id"), "attachment ID"),
        title=_required_text(selected.get("title"), "attachment title"),
        filename=_required_text(selected.get("filename"), "attachment filename"),
        url=url,
        content_type=_optional_text(selected.get("content_type")) or "text/csv",
        file_size=file_size,
        public_updated_at=_optional_text(payload.get("public_updated_at")),
    )


def _required_text(value: object, field_name: str) -> str:
    result = _optional_text(value)
    if result is None:
        raise REPDSchemaError(f"{field_name} is missing")
    return result


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _optional_positive_integer(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise REPDSchemaError("attachment file size must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise REPDSchemaError("attachment file size must be an integer") from exc
    if result <= 0:
        raise REPDSchemaError("attachment file size must be positive")
    return result
