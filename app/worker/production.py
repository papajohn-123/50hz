"""Production source-client lifecycle and polling policy.

The cadences intentionally reflect upstream publication behavior rather than a
single global timer.  Polling slightly faster than publication bounds detection
latency while overlap windows and idempotent source keys make retries safe.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from app.sources.client import (
    DEFAULT_ELEXON_BASE_URL,
    DEFAULT_NESO_CARBON_BASE_URL,
    AsyncJSONClient,
)
from app.sources.elexon import (
    FuelInstGenerationAdapter,
    InitialDemandAdapter,
    InterconnectorFlowAdapter,
    NationalDemandForecastAdapter,
    RemitUnavailabilityAdapter,
    SystemFrequencyAdapter,
    SystemWarningsAdapter,
    WindGenerationForecastAdapter,
)
from app.sources.neso_carbon import (
    LondonCarbonIntensityAdapter,
    NationalCarbonCurrentAdapter,
    NationalCarbonForecastAdapter,
)
from app.sources.types import AdapterResult, ObservationWindow, SourceAdapter
from app.worker.scheduler import PollSchedule


ELEXON_JOB_IDS = frozenset(
    {
        "elexon.fuelinst",
        "elexon.indo",
        "elexon.freq",
        "elexon.interconnectors",
        "elexon.ndf",
        "elexon.windfor",
        "elexon.remit.unavailability",
        "elexon.syswarn",
    }
)

CARBON_JOB_IDS = frozenset(
    {
        "neso.carbon.national.current",
        "neso.carbon.regional.london",
        "neso.carbon.national.forecast",
    }
)


class ForwardWindowAdapter:
    """Translate a scheduler tick window into a forward forecast horizon.

    ``PollSchedule`` checkpoints describe when polling occurred, so the returned
    result retains the scheduler window.  The actual forward query window is
    recorded in metadata for auditability.
    """

    def __init__(
        self,
        adapter: SourceAdapter[Any],
        *,
        horizon: timedelta,
    ) -> None:
        if horizon <= timedelta(0):
            raise ValueError("forecast horizon must be positive")
        self.adapter = adapter
        self.horizon = horizon
        self.source_id = adapter.source_id
        self.dataset = adapter.dataset
        self.endpoint = adapter.endpoint

    async def fetch(self, window: ObservationWindow) -> AdapterResult[Any]:
        anchor = _floor_half_hour(window.end)
        forecast_window = ObservationWindow(start=anchor, end=anchor + self.horizon)
        result = await self.adapter.fetch(forecast_window)
        metadata = dict(result.metadata)
        metadata.update(
            {
                "forecastWindowStart": forecast_window.start.isoformat(),
                "forecastWindowEnd": forecast_window.end.isoformat(),
            }
        )
        return replace(result, window=window, metadata=metadata)


def build_production_schedules(
    *,
    elexon_client: AsyncJSONClient,
    carbon_client: AsyncJSONClient,
) -> tuple[PollSchedule, ...]:
    """Return every continuously collected production source schedule.

    Postcodes are intentionally absent: postcode carbon is fetched on demand and
    cached for the half-hour rather than multiplied across users in this worker.
    """

    carbon_forecast = ForwardWindowAdapter(
        NationalCarbonForecastAdapter(carbon_client),
        horizon=timedelta(hours=48),
    )

    schedules = (
        # FUELINST is published every five minutes.  A two-minute poll limits
        # detection latency to two minutes without approaching source rate limits.
        PollSchedule(
            job_id="elexon.fuelinst",
            adapter=FuelInstGenerationAdapter(elexon_client),
            cadence=timedelta(minutes=2),
            overlap=timedelta(minutes=10),
            initial_lookback=timedelta(minutes=30),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=1),
            reconcile_lookback=timedelta(hours=48),
        ),
        # INDO updates every 15 minutes.  Polling every five minutes catches it
        # promptly without the previous two-minute no-op traffic.
        PollSchedule(
            job_id="elexon.indo",
            adapter=InitialDemandAdapter(elexon_client),
            cadence=timedelta(minutes=5),
            overlap=timedelta(hours=1),
            initial_lookback=timedelta(hours=2),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=1),
            reconcile_lookback=timedelta(hours=48),
        ),
        # FREQ files arrive every two minutes and contain 15-second observations.
        PollSchedule(
            job_id="elexon.freq",
            adapter=SystemFrequencyAdapter(elexon_client),
            cadence=timedelta(minutes=1),
            overlap=timedelta(minutes=10),
            initial_lookback=timedelta(minutes=10),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=1),
            reconcile_lookback=timedelta(hours=48),
        ),
        PollSchedule(
            job_id="elexon.interconnectors",
            adapter=InterconnectorFlowAdapter(elexon_client),
            cadence=timedelta(minutes=2),
            overlap=timedelta(minutes=10),
            initial_lookback=timedelta(minutes=30),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=1),
            reconcile_lookback=timedelta(hours=48),
        ),
        # NESO Carbon Intensity values cover half-hour periods.  The current
        # endpoints need no historic reconciliation because they expose one slot.
        PollSchedule(
            job_id="neso.carbon.national.current",
            adapter=NationalCarbonCurrentAdapter(carbon_client),
            cadence=timedelta(minutes=15),
            overlap=timedelta(minutes=5),
            initial_lookback=timedelta(minutes=30),
            max_incremental_lookback=timedelta(hours=2),
            reconcile_every=None,
            reconcile_lookback=timedelta(hours=1),
        ),
        PollSchedule(
            job_id="neso.carbon.regional.london",
            adapter=LondonCarbonIntensityAdapter(carbon_client),
            cadence=timedelta(minutes=15),
            overlap=timedelta(minutes=5),
            initial_lookback=timedelta(minutes=30),
            max_incremental_lookback=timedelta(hours=2),
            reconcile_every=None,
            reconcile_lookback=timedelta(hours=1),
        ),
        PollSchedule(
            job_id="neso.carbon.national.forecast",
            adapter=carbon_forecast,
            cadence=timedelta(minutes=30),
            overlap=timedelta(minutes=5),
            initial_lookback=timedelta(minutes=30),
            max_incremental_lookback=timedelta(hours=2),
            reconcile_every=None,
            reconcile_lookback=timedelta(hours=1),
        ),
        # NDF is refreshed every 30 minutes.  Two hours of overlap retains late
        # and revised forecast publications; six-hour reconciliation is ample.
        PollSchedule(
            job_id="elexon.ndf",
            adapter=NationalDemandForecastAdapter(elexon_client),
            cadence=timedelta(minutes=15),
            overlap=timedelta(hours=2),
            initial_lookback=timedelta(hours=6),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=6),
            reconcile_lookback=timedelta(hours=48),
        ),
        # WINDFOR publishes at most eight batches per day.  A 30-minute check and
        # 12-hour overlap catches the irregular publication times economically.
        PollSchedule(
            job_id="elexon.windfor",
            adapter=WindGenerationForecastAdapter(elexon_client),
            cadence=timedelta(minutes=30),
            overlap=timedelta(hours=12),
            initial_lookback=timedelta(hours=24),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=12),
            reconcile_lookback=timedelta(hours=48),
        ),
        # REMIT and SYSWARN are event-driven.  Their overlap is deliberately much
        # larger than cadence so corrections around restarts are not lost.
        PollSchedule(
            job_id="elexon.remit.unavailability",
            adapter=RemitUnavailabilityAdapter(elexon_client),
            cadence=timedelta(minutes=2),
            overlap=timedelta(minutes=30),
            initial_lookback=timedelta(hours=2),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=1),
            reconcile_lookback=timedelta(hours=48),
        ),
        PollSchedule(
            job_id="elexon.syswarn",
            adapter=SystemWarningsAdapter(elexon_client),
            cadence=timedelta(minutes=5),
            overlap=timedelta(hours=1),
            initial_lookback=timedelta(hours=24),
            max_incremental_lookback=timedelta(hours=48),
            reconcile_every=timedelta(hours=6),
            reconcile_lookback=timedelta(hours=48),
        ),
    )
    if len({schedule.job_id for schedule in schedules}) != len(schedules):
        raise ValueError("production poll schedule job IDs must be unique")
    return schedules


@dataclass(slots=True)
class ProductionSourceBundle:
    """Own both source clients and their production schedules as one lifecycle."""

    elexon_client: AsyncJSONClient
    carbon_client: AsyncJSONClient
    schedules: tuple[PollSchedule, ...]

    @classmethod
    def create(cls) -> ProductionSourceBundle:
        elexon_client = AsyncJSONClient(base_url=DEFAULT_ELEXON_BASE_URL)
        carbon_client = AsyncJSONClient(base_url=DEFAULT_NESO_CARBON_BASE_URL)
        return cls(
            elexon_client=elexon_client,
            carbon_client=carbon_client,
            schedules=build_production_schedules(
                elexon_client=elexon_client,
                carbon_client=carbon_client,
            ),
        )

    async def aclose(self) -> None:
        await self.elexon_client.aclose()
        await self.carbon_client.aclose()

    async def __aenter__(self) -> ProductionSourceBundle:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def _floor_half_hour(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("forecast anchor must be timezone-aware")
    value = value.astimezone(UTC)
    minute = 30 if value.minute >= 30 else 0
    return value.replace(minute=minute, second=0, microsecond=0)

