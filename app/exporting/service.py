from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Protocol

from app.exporting.models import (
    ExportCoverage,
    ExportRequest,
    ExportResponse,
    ExportRow,
    ExportRowStatus,
)
from app.history.materialize import materialize_half_hours
from app.history.repository import (
    FUELINST_SOURCE_ID,
    INDO_SOURCE_ID,
    NATIONAL_CARBON_SOURCE_ID,
    HistoryMetric,
    HistorySeriesRequest,
)


class HistoryLoader(Protocol):
    async def load(self, request: HistorySeriesRequest): ...


def _source_id(metric: HistoryMetric) -> str:
    if metric is HistoryMetric.NATIONAL_CARBON:
        return NATIONAL_CARBON_SOURCE_ID
    if metric is HistoryMetric.NATIONAL_DEMAND:
        return INDO_SOURCE_ID
    if metric in {
        HistoryMetric.GENERATION_FUEL,
        HistoryMetric.INTERCONNECTOR_FLOW,
    }:
        return FUELINST_SOURCE_ID
    raise ValueError("unsupported export metric")


async def build_export(
    repository: HistoryLoader,
    request: ExportRequest,
    *,
    generated_at: datetime | None = None,
) -> ExportResponse:
    """Build a finite half-hour export without interpolation or hidden gaps."""

    if not isinstance(request, ExportRequest):
        raise TypeError("request must be an ExportRequest")
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    timestamp = timestamp.astimezone(UTC)

    history_request = HistorySeriesRequest(
        metric_id=request.metric,
        source_id=_source_id(request.metric),
        selector=request.selector,
        start=request.start,
        end=request.end,
    )
    raw = await repository.load(history_request)
    result = materialize_half_hours(
        raw,
        start=request.start,
        end=request.end,
    )

    rows: list[ExportRow] = []
    for interval in result.intervals:
        source_record_ids = sorted(
            {
                record_id
                for sample in interval.selected_samples
                for record_id in sample.source_record_ids
            }
        )
        available = interval.value is not None
        rows.append(
            ExportRow(
                start=interval.start,
                end=interval.end,
                status=(
                    ExportRowStatus.AVAILABLE
                    if available
                    else ExportRowStatus.INSUFFICIENT_DATA
                ),
                value=interval.value,
                unit=result.identity.unit,
                classification=result.identity.fact_class,
                metric_id=result.identity.metric_id,
                geography=result.identity.geography,
                source_id=result.identity.source_id,
                source_record_ids=source_record_ids if available else [],
                source_methodology_version=result.identity.methodology_version,
                materialization_methodology_version=result.methodology.version,
                coverage_fraction=interval.coverage_fraction,
            )
        )

    available_count = sum(
        row.status is ExportRowStatus.AVAILABLE for row in rows
    )
    expected_count = len(rows)
    return ExportResponse(
        generated_at=timestamp,
        requested_from=request.start.astimezone(UTC),
        requested_to=request.end.astimezone(UTC),
        metric_id=result.identity.metric_id,
        geography=result.identity.geography,
        unit=result.identity.unit,
        classification=result.identity.fact_class,
        source_id=result.identity.source_id,
        source_methodology_version=result.identity.methodology_version,
        materialization_methodology_version=result.methodology.version,
        coverage=ExportCoverage(
            expected_interval_count=expected_count,
            available_interval_count=available_count,
            missing_interval_count=expected_count - available_count,
            coverage_fraction=available_count / expected_count,
            is_complete=available_count == expected_count,
        ),
        rows=rows,
    )


CSV_COLUMNS = (
    "start",
    "end",
    "status",
    "value",
    "unit",
    "classification",
    "metric_id",
    "geography",
    "source_id",
    "source_record_ids",
    "source_methodology_version",
    "materialization_methodology_version",
    "coverage_fraction",
)


def render_csv(response: ExportResponse) -> str:
    """Render stable UTF-8 CSV columns, retaining explicit missing rows."""

    if not isinstance(response, ExportResponse):
        raise TypeError("response must be an ExportResponse")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in response.rows:
        writer.writerow(
            {
                "start": row.start.astimezone(UTC).isoformat(),
                "end": row.end.astimezone(UTC).isoformat(),
                "status": row.status.value,
                "value": "" if row.value is None else repr(row.value),
                "unit": row.unit,
                "classification": row.classification,
                "metric_id": row.metric_id,
                "geography": row.geography,
                "source_id": row.source_id,
                "source_record_ids": "|".join(row.source_record_ids),
                "source_methodology_version": row.source_methodology_version,
                "materialization_methodology_version": (
                    row.materialization_methodology_version
                ),
                "coverage_fraction": repr(row.coverage_fraction),
            }
        )
    return output.getvalue()
