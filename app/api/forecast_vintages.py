"""Truthful grouping of persisted national carbon forecasts by capture vintage."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.charging import CarbonForecastPoint, CarbonForecastSeries
from app.persistence import ForecastRead


NATIONAL_FORECAST_GEOGRAPHY = "GB"
NATIONAL_FORECAST_SCOPE = "national"
NATIONAL_FORECAST_METHODOLOGY_VERSION = (
    "50hz.neso-carbon-intensity.national-forecast.v1"
)
SOURCE_ISSUE_TIME_UNAVAILABLE = "source_does_not_publish_issue_time"
CAPTURE_TIME_ISSUE_BASIS = "retrieved_at"


class ForecastHistoryRepository(Protocol):
    async def get_carbon_forecast_history(
        self,
        *,
        region_code: str,
        window_start: datetime,
        window_end: datetime,
        captured_after: datetime,
        captured_before: datetime,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]: ...


@dataclass(frozen=True, slots=True)
class NationalForecastVintage:
    series_id: str
    source_id: str
    model_name: str
    methodology_version: str
    captured_at: datetime
    vintage_at: datetime
    source_issued_at: datetime | None
    issue_time_basis: str
    rows: tuple[ForecastRead, ...]

    @property
    def horizon_end(self) -> datetime:
        return max(row.valid_to for row in self.rows if row.valid_to is not None)

    @property
    def source_record_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                _source_record_id(row)
                for row in sorted(self.rows, key=lambda item: item.valid_from)
            )
        )

    def as_series(self) -> CarbonForecastSeries:
        return CarbonForecastSeries(
            series_id=self.series_id,
            geography=NATIONAL_FORECAST_GEOGRAPHY,
            source_id=self.source_id,
            methodology_version=self.methodology_version,
            vintage_at=self.vintage_at,
            points=[
                CarbonForecastPoint(
                    start=row.valid_from,
                    end=row.valid_to,
                    intensity_gco2_kwh=row.value,
                    source_record_id=_source_record_id(row),
                )
                for row in self.rows
                if row.valid_to is not None
            ],
        )

    def matching_interval(
        self,
        start: datetime,
        end: datetime,
    ) -> ForecastRead | None:
        return next(
            (
                row
                for row in self.rows
                if row.valid_to is not None
                and row.valid_from == start
                and row.valid_to == end
            ),
            None,
        )


async def load_national_forecast_vintages(
    repository: ForecastHistoryRepository,
    *,
    window_start: datetime,
    window_end: datetime,
    captured_before: datetime,
    capture_lookback: timedelta,
) -> tuple[NationalForecastVintage, ...]:
    rows = await repository.get_carbon_forecast_history(
        region_code=NATIONAL_FORECAST_GEOGRAPHY,
        window_start=window_start,
        window_end=window_end,
        captured_after=captured_before - capture_lookback,
        captured_before=captured_before,
        issued_before=captured_before,
    )
    return group_national_forecast_vintages(rows)


def group_national_forecast_vintages(
    rows: tuple[ForecastRead, ...],
) -> tuple[NationalForecastVintage, ...]:
    grouped: dict[
        tuple[str, str, datetime, datetime, str],
        list[ForecastRead],
    ] = defaultdict(list)
    for row in rows:
        if not _is_supported_national_forecast(row):
            continue
        basis = str(row.attributes.get("issueTimeBasis") or "").strip().casefold()
        normalized_basis = basis or (
            "published_at" if row.published_at is not None else "retrieved_at"
        )
        key = (
            row.source_id,
            row.model_name or "neso_carbon_intensity",
            row.issued_at,
            row.retrieved_at,
            normalized_basis,
        )
        grouped[key].append(row)

    vintages: list[NationalForecastVintage] = []
    for (
        source_id,
        model_name,
        issued_at,
        captured_at,
        raw_issue_basis,
    ), values in grouped.items():
        by_start: dict[datetime, ForecastRead] = {}
        for row in sorted(
            values,
            key=lambda item: (
                item.valid_from,
                item.source_record_id or "",
            ),
        ):
            by_start[row.valid_from] = row
        issue_is_capture = raw_issue_basis == CAPTURE_TIME_ISSUE_BASIS
        source_issued_at = None if issue_is_capture else issued_at
        vintages.append(
            NationalForecastVintage(
                series_id=(
                    f"{source_id}:carbon-intensity:"
                    f"{NATIONAL_FORECAST_GEOGRAPHY}:{model_name}"
                ),
                source_id=source_id,
                model_name=model_name,
                methodology_version=NATIONAL_FORECAST_METHODOLOGY_VERSION,
                captured_at=captured_at,
                vintage_at=issued_at,
                source_issued_at=source_issued_at,
                issue_time_basis=(
                    SOURCE_ISSUE_TIME_UNAVAILABLE
                    if issue_is_capture
                    else raw_issue_basis
                ),
                rows=tuple(by_start[start] for start in sorted(by_start)),
            )
        )

    return tuple(
        sorted(
            vintages,
            key=lambda vintage: (
                vintage.captured_at,
                vintage.vintage_at,
                vintage.source_id,
            ),
            reverse=True,
        )
    )


def _is_supported_national_forecast(row: ForecastRead) -> bool:
    classification = str(row.attributes.get("classification") or "").casefold()
    return (
        row.metric_type == "carbon_intensity"
        and row.series_key.casefold() == NATIONAL_FORECAST_GEOGRAPHY.casefold()
        and classification == "forecast"
        and row.valid_to is not None
        and row.valid_to > row.valid_from
        and row.unit.casefold().replace("₂", "2") == "gco2/kwh"
        and math.isfinite(row.value)
        and row.value >= 0
    )


def _source_record_id(row: ForecastRead) -> str:
    return row.source_record_id or (
        f"{row.source_id}:{row.valid_from.isoformat()}:{row.issued_at.isoformat()}"
    )
