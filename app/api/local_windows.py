"""Application service exposing the deterministic flexible-use planner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.api.forecast_vintages import (
    ForecastHistoryRepository,
    NationalForecastVintage,
    load_national_forecast_vintages,
)
from app.api.models import (
    LocalFlexibleUsePlan,
    LocalForecastMetadata,
    LocalSearchBounds,
    LocalWindowsResponse,
)
from app.charging import FlexibleUsePlan, plan_flexible_use
from app.sources.neso_carbon import normalize_outward_postcode


DEFAULT_SEARCH_HORIZON = timedelta(hours=24)
MAX_SEARCH_HORIZON = timedelta(hours=48)
MAX_FORECAST_CAPTURE_AGE = timedelta(minutes=90)
FORECAST_VINTAGE_LOOKBACK = timedelta(hours=24)
DEFAULT_BOUNDS_RULE = (
    "When omitted, earliest is the next UTC half-hour at or after evaluation; "
    "latest is 24 hours after that boundary, capped to the selected forecast horizon."
)


class LocalWindowsValidationError(ValueError):
    pass


class LocalWindowsUnavailableError(RuntimeError):
    pass


async def present_local_windows(
    repository: ForecastHistoryRepository,
    *,
    postcode: str,
    now: datetime,
    duration_minutes: int,
    earliest: datetime | None = None,
    latest: datetime | None = None,
    continuous: bool = True,
) -> LocalWindowsResponse:
    """Build one privacy-safe plan from one compatible national forecast vintage."""

    normalized_postcode = _normalized_postcode(postcode)
    instant = _aware_utc(now, "now")
    duration = _duration(duration_minutes)
    if continuous is not True:
        raise LocalWindowsValidationError(
            "continuous must be true; interruptible windows are not supported"
        )

    default_earliest = _ceil_half_hour(instant)
    earliest_start = (
        _exact_half_hour(earliest, "earliest")
        if earliest is not None
        else default_earliest
    )
    if earliest_start < default_earliest:
        raise LocalWindowsValidationError(
            "earliest cannot be before the next available half-hour boundary"
        )

    default_latest = default_earliest + DEFAULT_SEARCH_HORIZON
    latest_finish = (
        _exact_half_hour(latest, "latest") if latest is not None else default_latest
    )
    _validate_search_span(
        earliest_start,
        latest_finish,
        duration=duration,
    )

    query_start = _floor_half_hour(instant)
    vintages = await load_national_forecast_vintages(
        repository,
        window_start=query_start,
        window_end=latest_finish,
        captured_before=instant,
        capture_lookback=FORECAST_VINTAGE_LOOKBACK,
    )
    if not vintages:
        raise LocalWindowsUnavailableError(
            "A national carbon forecast is missing or unavailable for the requested bounds"
        )

    fresh_vintages = tuple(
        vintage
        for vintage in vintages
        if _capture_age(instant, vintage.captured_at)
        <= int(MAX_FORECAST_CAPTURE_AGE.total_seconds())
    )
    if not fresh_vintages:
        raise LocalWindowsUnavailableError(
            "The latest national carbon forecast capture is stale"
        )

    selection = _select_vintage_and_plan(
        fresh_vintages,
        duration=duration,
        earliest_start=earliest_start,
        requested_latest=latest_finish,
        latest_was_defaulted=latest is None,
        start_now=instant,
    )
    if selection is None:
        if latest is None and all(
            min(default_latest, vintage.horizon_end) - earliest_start < duration
            for vintage in fresh_vintages
        ):
            raise LocalWindowsValidationError(
                "The default forecast horizon cannot fit durationMinutes"
            )
        raise LocalWindowsUnavailableError(
            "No internally compatible national carbon forecast vintage is usable"
        )

    vintage, plan = selection
    local_plan = LocalFlexibleUsePlan.model_validate(
        plan.model_dump(mode="python")
    )
    return LocalWindowsResponse(
        postcode=normalized_postcode,
        evaluated_at=instant,
        bounds=LocalSearchBounds(
            earliest_start=local_plan.earliest_start,
            latest_finish=local_plan.latest_finish,
            earliest_was_defaulted=earliest is None,
            latest_was_defaulted=latest is None,
            default_rule=DEFAULT_BOUNDS_RULE,
        ),
        forecast=LocalForecastMetadata(
            series_id=vintage.series_id,
            source_id=vintage.source_id,
            methodology_version=vintage.methodology_version,
            source_issued_at=vintage.source_issued_at,
            captured_at=vintage.captured_at,
            vintage_at=vintage.vintage_at,
            vintage_basis=(
                "captured_at"
                if vintage.source_issued_at is None
                else "source_issued_at"
            ),
            issue_time_basis=vintage.issue_time_basis,
            capture_age_seconds=_capture_age(instant, vintage.captured_at),
            capture_stale_after_seconds=int(
                MAX_FORECAST_CAPTURE_AGE.total_seconds()
            ),
            source_record_ids=list(vintage.source_record_ids),
        ),
        plan=local_plan,
        limitations=[
            "The postcode is used only to identify the user's Local view; the "
            "planning forecast is Great Britain national, not regional or postcode-level.",
            "The comparison reports forecast carbon-intensity differences only; "
            "it does not claim emissions, cost, or financial savings.",
        ],
    )


def _select_vintage_and_plan(
    vintages: tuple[NationalForecastVintage, ...],
    *,
    duration: timedelta,
    earliest_start: datetime,
    requested_latest: datetime,
    latest_was_defaulted: bool,
    start_now: datetime,
) -> tuple[NationalForecastVintage, FlexibleUsePlan] | None:
    incomplete: tuple[NationalForecastVintage, FlexibleUsePlan] | None = None
    for vintage in vintages:
        latest_finish = (
            min(requested_latest, vintage.horizon_end)
            if latest_was_defaulted
            else requested_latest
        )
        if latest_finish - earliest_start < duration:
            continue
        try:
            plan = plan_flexible_use(
                vintage.as_series(),
                duration=duration,
                earliest_start=earliest_start,
                latest_finish=latest_finish,
                start_now=start_now,
                continuous=True,
            )
        except (TypeError, ValueError):
            continue
        if plan.recommended_window is not None:
            return vintage, plan
        if incomplete is None:
            incomplete = (vintage, plan)
    return incomplete


def _duration(value: int) -> timedelta:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LocalWindowsValidationError("durationMinutes must be an integer")
    if not 30 <= value <= 720:
        raise LocalWindowsValidationError(
            "durationMinutes must be between 30 and 720"
        )
    if value % 30:
        raise LocalWindowsValidationError(
            "durationMinutes must be a whole number of half-hour intervals"
        )
    return timedelta(minutes=value)


def _validate_search_span(
    earliest: datetime,
    latest: datetime,
    *,
    duration: timedelta,
) -> None:
    if latest <= earliest:
        raise LocalWindowsValidationError("latest must be after earliest")
    if latest - earliest < duration:
        raise LocalWindowsValidationError(
            "The requested bounds cannot fit durationMinutes"
        )
    if latest - earliest > MAX_SEARCH_HORIZON:
        raise LocalWindowsValidationError(
            "The requested search horizon cannot exceed 48 hours"
        )


def _exact_half_hour(value: datetime, field: str) -> datetime:
    instant = _aware_utc(value, field)
    if (
        instant.minute not in (0, 30)
        or instant.second != 0
        or instant.microsecond != 0
    ):
        raise LocalWindowsValidationError(
            f"{field} must be exactly on a half-hour boundary"
        )
    return instant


def _ceil_half_hour(value: datetime) -> datetime:
    floor = _floor_half_hour(value)
    return floor if floor == value else floor + timedelta(minutes=30)


def _floor_half_hour(value: datetime) -> datetime:
    instant = value.astimezone(UTC)
    return instant.replace(
        minute=30 if instant.minute >= 30 else 0,
        second=0,
        microsecond=0,
    )


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise LocalWindowsValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise LocalWindowsValidationError(f"{field} must include a timezone")
    return value.astimezone(UTC)


def _normalized_postcode(value: str) -> str:
    try:
        return normalize_outward_postcode(value)
    except (TypeError, ValueError) as error:
        raise LocalWindowsValidationError(str(error)) from error


def _capture_age(evaluated_at: datetime, captured_at: datetime) -> int:
    return max(0, int((evaluated_at - captured_at).total_seconds()))
