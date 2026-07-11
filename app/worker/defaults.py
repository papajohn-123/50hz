"""Initial production polling policy for the four Elexon live families."""

from __future__ import annotations

from datetime import timedelta

from app.sources.client import AsyncJSONClient
from app.sources.elexon import (
    FuelInstGenerationAdapter,
    InitialDemandAdapter,
    InterconnectorFlowAdapter,
    SystemFrequencyAdapter,
)
from app.worker.scheduler import PollSchedule


def build_elexon_schedules(client: AsyncJSONClient) -> tuple[PollSchedule, ...]:
    shared = {
        "max_incremental_lookback": timedelta(hours=48),
        "reconcile_every": timedelta(hours=1),
        "reconcile_lookback": timedelta(hours=48),
    }
    return (
        PollSchedule(
            job_id="elexon.fuelinst",
            adapter=FuelInstGenerationAdapter(client),
            cadence=timedelta(minutes=2),
            overlap=timedelta(minutes=10),
            initial_lookback=timedelta(minutes=30),
            **shared,
        ),
        PollSchedule(
            job_id="elexon.indo",
            adapter=InitialDemandAdapter(client),
            cadence=timedelta(minutes=2),
            overlap=timedelta(hours=1),
            initial_lookback=timedelta(hours=2),
            **shared,
        ),
        PollSchedule(
            job_id="elexon.freq",
            adapter=SystemFrequencyAdapter(client),
            cadence=timedelta(minutes=1),
            overlap=timedelta(minutes=10),
            initial_lookback=timedelta(minutes=10),
            **shared,
        ),
        PollSchedule(
            job_id="elexon.interconnectors",
            adapter=InterconnectorFlowAdapter(client),
            cadence=timedelta(minutes=2),
            overlap=timedelta(minutes=10),
            initial_lookback=timedelta(minutes=30),
            **shared,
        ),
    )

