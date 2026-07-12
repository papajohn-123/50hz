from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.history.repository import HistoryMetric


MAX_EXPORT_WINDOW = timedelta(days=31)
EXPORT_RESOLUTION_SECONDS = 1_800


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ExportModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


class ExportRowStatus(StrEnum):
    AVAILABLE = "available"
    INSUFFICIENT_DATA = "insufficient_data"


class ExportRequest(BaseModel):
    """An allow-listed export request with exact, bounded UTC boundaries."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    metric: HistoryMetric
    start: AwareDatetime
    end: AwareDatetime
    selector: str | None = None
    resolution_seconds: int = Field(default=EXPORT_RESOLUTION_SECONDS)
    format: ExportFormat = ExportFormat.JSON

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        start = self.start.astimezone(UTC)
        end = self.end.astimezone(UTC)
        for name, value in (("start", start), ("end", end)):
            if value.minute not in (0, 30) or value.second or value.microsecond:
                raise ValueError(f"{name} must be on an exact UTC half-hour boundary")
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > MAX_EXPORT_WINDOW:
            raise ValueError("export window cannot exceed 31 days")
        if self.resolution_seconds != EXPORT_RESOLUTION_SECONDS:
            raise ValueError("only 1800-second export resolution is supported")
        return self


class ExportCoverage(ExportModel):
    expected_interval_count: int = Field(ge=1)
    available_interval_count: int = Field(ge=0)
    missing_interval_count: int = Field(ge=0)
    coverage_fraction: float = Field(ge=0, le=1)
    is_complete: bool

    @model_validator(mode="after")
    def counts_are_consistent(self) -> Self:
        if self.available_interval_count + self.missing_interval_count != (
            self.expected_interval_count
        ):
            raise ValueError("export coverage counts are inconsistent")
        if self.is_complete != (self.missing_interval_count == 0):
            raise ValueError("export completeness does not match its gaps")
        return self


class ExportRow(ExportModel):
    start: AwareDatetime
    end: AwareDatetime
    status: ExportRowStatus
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str
    classification: str
    metric_id: str = Field(alias="metricID")
    geography: str
    source_id: str = Field(alias="sourceID")
    source_record_ids: list[str] = Field(alias="sourceRecordIDs")
    source_methodology_version: str
    materialization_methodology_version: str
    coverage_fraction: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def status_matches_value(self) -> Self:
        if self.status is ExportRowStatus.AVAILABLE:
            if self.value is None or not self.source_record_ids:
                raise ValueError("available export rows require a value and provenance")
        elif self.value is not None or self.source_record_ids:
            raise ValueError("missing export rows cannot expose value provenance")
        return self


class ExportResponse(ExportModel):
    schema_version: str = "1.0"
    generated_at: AwareDatetime
    requested_from: AwareDatetime
    requested_to: AwareDatetime
    resolution_seconds: int = EXPORT_RESOLUTION_SECONDS
    metric_id: str = Field(alias="metricID")
    geography: str
    unit: str
    classification: str
    source_id: str = Field(alias="sourceID")
    source_methodology_version: str
    materialization_methodology_version: str
    coverage: ExportCoverage
    rows: list[ExportRow] = Field(min_length=1, max_length=1_488)

    @model_validator(mode="after")
    def row_count_matches_coverage(self) -> Self:
        if len(self.rows) != self.coverage.expected_interval_count:
            raise ValueError("export row count does not match expected coverage")
        return self
