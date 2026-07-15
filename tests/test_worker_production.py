from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.sources.client import AsyncJSONClient
from app.sources.types import ObservationWindow
from app.worker.production import (
    CARBON_JOB_IDS,
    ELEXON_JOB_IDS,
    ForwardWindowAdapter,
    ProductionSourceBundle,
    UKPN_JOB_IDS,
    build_production_schedules,
)


def clients() -> tuple[AsyncJSONClient, AsyncJSONClient, AsyncJSONClient]:
    empty = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": []}, request=request)
    )
    return (
        AsyncJSONClient(
            base_url="https://elexon.example.test/",
            transport=empty,
        ),
        AsyncJSONClient(
            base_url="https://carbon.example.test/",
            transport=empty,
        ),
        AsyncJSONClient(
            base_url="https://ukpn.example.test/",
            transport=empty,
        ),
    )


def test_production_plan_has_every_continuous_job_once_and_no_postcode_job() -> None:
    elexon, carbon, ukpn = clients()
    try:
        schedules = build_production_schedules(
            elexon_client=elexon,
            carbon_client=carbon,
            ukpn_client=ukpn,
        )
    finally:
        asyncio.run(elexon.aclose())
        asyncio.run(carbon.aclose())
        asyncio.run(ukpn.aclose())

    job_ids = {schedule.job_id for schedule in schedules}
    assert len(schedules) == 15
    assert job_ids == ELEXON_JOB_IDS | CARBON_JOB_IDS | UKPN_JOB_IDS
    assert len(job_ids) == len(schedules)
    assert all("postcode" not in job_id for job_id in job_ids)
    assert len({schedule.lock_name for schedule in schedules}) == len(schedules)


def test_production_jobs_use_the_correct_separate_client() -> None:
    elexon, carbon, ukpn = clients()
    try:
        schedules = build_production_schedules(
            elexon_client=elexon,
            carbon_client=carbon,
            ukpn_client=ukpn,
        )
        for schedule in schedules:
            adapter = schedule.adapter
            underlying = (
                adapter.adapter
                if isinstance(adapter, ForwardWindowAdapter)
                else adapter
            )
            if schedule.job_id in ELEXON_JOB_IDS:
                assert underlying.client is elexon
            elif schedule.job_id in CARBON_JOB_IDS:
                assert underlying.client is carbon
            else:
                assert underlying.client is ukpn
        assert len({id(elexon), id(carbon), id(ukpn)}) == 3
    finally:
        asyncio.run(elexon.aclose())
        asyncio.run(carbon.aclose())
        asyncio.run(ukpn.aclose())


def test_source_aware_poll_and_overlap_policy() -> None:
    elexon, carbon, ukpn = clients()
    try:
        schedules = {
            schedule.job_id: schedule
            for schedule in build_production_schedules(
                elexon_client=elexon,
                carbon_client=carbon,
                ukpn_client=ukpn,
            )
        }
    finally:
        asyncio.run(elexon.aclose())
        asyncio.run(carbon.aclose())
        asyncio.run(ukpn.aclose())

    expected = {
        "elexon.bm-unit-reference": (timedelta(days=1), timedelta(hours=1)),
        "elexon.pn": (timedelta(minutes=10), timedelta(minutes=10)),
        "elexon.b1610": (timedelta(days=1), timedelta(hours=1)),
        "elexon.fuelinst": (timedelta(minutes=2), timedelta(minutes=10)),
        "elexon.indo": (timedelta(minutes=5), timedelta(hours=1)),
        "elexon.freq": (timedelta(minutes=1), timedelta(minutes=10)),
        "elexon.interconnectors": (
            timedelta(minutes=2),
            timedelta(minutes=10),
        ),
        "neso.carbon.national.current": (
            timedelta(minutes=5),
            timedelta(minutes=5),
        ),
        "neso.carbon.regional.london": (
            timedelta(minutes=5),
            timedelta(minutes=5),
        ),
        "neso.carbon.national.forecast": (
            timedelta(minutes=30),
            timedelta(minutes=5),
        ),
        "elexon.ndf": (timedelta(minutes=15), timedelta(hours=2)),
        "elexon.windfor": (timedelta(minutes=30), timedelta(hours=12)),
        "elexon.remit.unavailability": (
            timedelta(minutes=2),
            timedelta(minutes=30),
        ),
        "elexon.syswarn": (timedelta(minutes=5), timedelta(hours=1)),
        "ukpn.live_faults": (timedelta(minutes=5), timedelta(minutes=10)),
    }
    assert {
        job_id: (schedule.cadence, schedule.overlap)
        for job_id, schedule in schedules.items()
    } == expected

    for job_id in CARBON_JOB_IDS:
        assert schedules[job_id].reconcile_every is None
    for job_id in UKPN_JOB_IDS:
        assert schedules[job_id].reconcile_every is None
    for job_id in {
        "elexon.bm-unit-reference",
        "elexon.pn",
        "elexon.b1610",
    }:
        assert schedules[job_id].reconcile_every is None
    assert schedules["elexon.remit.unavailability"].reconcile_every == timedelta(
        hours=1
    )
    assert schedules["elexon.ndf"].reconcile_every == timedelta(hours=6)
    assert schedules["elexon.windfor"].reconcile_every == timedelta(hours=12)
    assert schedules["elexon.syswarn"].reconcile_every == timedelta(hours=6)


def test_bm_unit_jobs_are_ordered_reference_first_and_never_poll_pn_too_fast() -> None:
    elexon, carbon, ukpn = clients()
    try:
        schedules = build_production_schedules(
            elexon_client=elexon,
            carbon_client=carbon,
            ukpn_client=ukpn,
        )
    finally:
        asyncio.run(elexon.aclose())
        asyncio.run(carbon.aclose())
        asyncio.run(ukpn.aclose())

    job_ids = [schedule.job_id for schedule in schedules]
    assert job_ids[:3] == [
        "elexon.bm-unit-reference",
        "elexon.pn",
        "elexon.b1610",
    ]
    by_id = {schedule.job_id: schedule for schedule in schedules}
    assert by_id["elexon.pn"].cadence >= timedelta(minutes=10)
    assert by_id["elexon.b1610"].cadence == timedelta(days=1)


def test_carbon_forecast_wrapper_queries_forward_48_hours_but_checkpoints_tick() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": []}, request=request)

    async def scenario():
        elexon = AsyncJSONClient(
            base_url="https://elexon.example.test/",
            transport=httpx.MockTransport(handler),
        )
        carbon = AsyncJSONClient(
            base_url="https://carbon.example.test/",
            transport=httpx.MockTransport(handler),
            clock=lambda: datetime(2026, 7, 11, 12, 48, tzinfo=UTC),
        )
        ukpn = AsyncJSONClient(
            base_url="https://ukpn.example.test/",
            transport=httpx.MockTransport(handler),
        )
        try:
            schedule = next(
                schedule
                for schedule in build_production_schedules(
                    elexon_client=elexon,
                    carbon_client=carbon,
                    ukpn_client=ukpn,
                )
                if schedule.job_id == "neso.carbon.national.forecast"
            )
            tick_window = ObservationWindow(
                start=datetime(2026, 7, 11, 12, 18, tzinfo=UTC),
                end=datetime(2026, 7, 11, 12, 48, tzinfo=UTC),
            )
            result = await schedule.adapter.fetch(tick_window)
            return result, tick_window
        finally:
            await elexon.aclose()
            await carbon.aclose()
            await ukpn.aclose()

    result, tick_window = asyncio.run(scenario())
    assert requests[0].url.path == (
        "/intensity/2026-07-11T12:30Z/2026-07-13T12:30Z"
    )
    assert result.window == tick_window
    assert result.metadata["forecastWindowStart"] == "2026-07-11T12:30:00+00:00"
    assert result.metadata["forecastWindowEnd"] == "2026-07-13T12:30:00+00:00"


def test_owned_bundle_creates_distinct_clients_and_closes_as_one_lifecycle() -> None:
    async def scenario() -> None:
        async with ProductionSourceBundle.create() as bundle:
            assert bundle.elexon_client is not bundle.carbon_client
            assert bundle.ukpn_client is not bundle.elexon_client
            assert bundle.ukpn_client is not bundle.carbon_client
            assert len(bundle.schedules) == 15

    asyncio.run(scenario())
