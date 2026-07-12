from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest

import app.history.backfill as backfill_module
from app.db.session import DatabaseNotConfiguredError
from app.history.backfill import (
    MAX_BACKFILL_DAYS,
    BackfillDateRange,
    BackfillRequest,
    BackfillSource,
    BackfillSourceSpec,
    BackfillStatus,
    HistoryBackfillRunner,
    NATIONAL_CARBON_EARLIEST_DATE,
    SOURCE_ORDER,
    _retain_records_in_window,
    backfill_job_id,
    build_backfill_sources,
    build_parser,
    main,
    plan_settlement_chunks,
    render_report,
    request_from_args,
    resolve_backfill_date_range,
)
from app.sources.client import AsyncJSONClient
from app.sources.types import (
    AdapterResult,
    DataClassification,
    DemandForecastRecord,
    ObservationWindow,
    RemitUnavailabilityRecord,
)
from app.worker.contracts import IngestionCheckpoint, PersistOutcome


NOW = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


class ScriptedAdapter:
    dataset = "TEST"
    endpoint = "mock/history"

    def __init__(self, source_id: str, *, fail_calls: set[int] | None = None) -> None:
        self.source_id = source_id
        self.fail_calls = fail_calls or set()
        self.windows: list[ObservationWindow] = []

    async def fetch(self, window: ObservationWindow) -> AdapterResult[Any]:
        call_number = len(self.windows)
        self.windows.append(window)
        if call_number in self.fail_calls:
            raise RuntimeError(
                "upstream failed token=do-not-print https://example.test/private?payload=full"
            )
        return AdapterResult(
            source_id=self.source_id,
            dataset=self.dataset,
            endpoint=self.endpoint,
            window=window,
            retrieved_at=NOW,
            request_url="https://example.test/mock/history",
            records=(),
            raw_payload={"data": ["must-not-be-printed"]},
            raw_body=b'{"data":["must-not-be-printed"]}',
            checksum_sha256="a" * 64,
            content_type="application/json",
        )


class FakeRepository:
    def __init__(self) -> None:
        self.checkpoints: dict[str, IngestionCheckpoint] = {}
        self.successes: list[tuple[str, AdapterResult[Any]]] = []
        self.failures: list[dict[str, Any]] = []

    async def get_checkpoint(self, job_id: str) -> IngestionCheckpoint | None:
        return self.checkpoints.get(job_id)

    async def persist_success(
        self,
        *,
        job_id: str,
        result: AdapterResult[Any],
        attempted_at: datetime,
        completed_at: datetime,
    ) -> PersistOutcome:
        assert completed_at >= attempted_at
        self.successes.append((job_id, result))
        return PersistOutcome(inserted=len(result.records))

    async def record_failure(self, **values: Any) -> None:
        self.failures.append(values)


class FakeLocks:
    def __init__(self, *, unavailable: set[str] | None = None) -> None:
        self.unavailable = unavailable or set()
        self.names: list[str] = []

    @asynccontextmanager
    async def acquire(self, lock_name: str):
        self.names.append(lock_name)
        yield lock_name not in self.unavailable


def runner_sources(
    *,
    failing_source: BackfillSource | None = None,
    failing_calls: set[int] | None = None,
) -> tuple[tuple[BackfillSourceSpec, ...], dict[BackfillSource, ScriptedAdapter]]:
    adapters: dict[BackfillSource, ScriptedAdapter] = {}
    specs: list[BackfillSourceSpec] = []
    for source in SOURCE_ORDER:
        adapter = ScriptedAdapter(
            source.value,
            fail_calls=failing_calls if source is failing_source else None,
        )
        adapters[source] = adapter
        specs.append(
            BackfillSourceSpec(
                source=source,
                adapter=adapter,
                chunk_days=1,
                lock_source_id=source.value,
            )
        )
    return tuple(specs), adapters


@pytest.mark.parametrize(
    ("settlement_day", "expected_periods", "expected_hours"),
    [
        (date(2026, 3, 29), 46, 23),
        (date(2026, 7, 11), 48, 24),
        (date(2026, 10, 25), 50, 25),
    ],
)
def test_one_day_chunks_cover_every_dst_settlement_period(
    settlement_day: date,
    expected_periods: int,
    expected_hours: int,
) -> None:
    chunks = plan_settlement_chunks(
        BackfillDateRange(
            start=settlement_day,
            end=settlement_day + timedelta(days=1),
        ),
        chunk_days=1,
    )

    assert len(chunks) == 1
    assert chunks[0].end - chunks[0].start == timedelta(hours=expected_hours)
    assert (chunks[0].end - chunks[0].start) / timedelta(minutes=30) == expected_periods


def test_chunks_are_contiguous_and_carbon_can_split_at_year_boundary() -> None:
    chunks = plan_settlement_chunks(
        BackfillDateRange(start=date(2026, 12, 28), end=date(2027, 1, 5)),
        chunk_days=7,
        split_at_year_boundary=True,
    )

    assert len(chunks) == 2
    assert chunks[0].start == datetime(2026, 12, 28, 0, 0, tzinfo=UTC)
    assert chunks[0].end == datetime(2027, 1, 1, 0, 0, tzinfo=UTC)
    assert chunks[1].start == chunks[0].end
    assert chunks[1].end == datetime(2027, 1, 5, 0, 0, tzinfo=UTC)
    assert backfill_job_id(BackfillSource.NATIONAL_CARBON, chunks[0]) != backfill_job_id(
        BackfillSource.NATIONAL_CARBON, chunks[1]
    )


def test_days_and_explicit_ranges_are_bounded_and_end_is_exclusive() -> None:
    today = date(2026, 7, 12)
    by_days = resolve_backfill_date_range(
        days=90,
        start=None,
        end=None,
        today=today,
    )
    explicit = resolve_backfill_date_range(
        days=None,
        start=by_days.start,
        end=today,
        today=today,
    )

    assert by_days == explicit
    assert by_days.day_count == 90
    with pytest.raises(ValueError, match=f"between 1 and {MAX_BACKFILL_DAYS}"):
        resolve_backfill_date_range(
            days=MAX_BACKFILL_DAYS + 1,
            start=None,
            end=None,
            today=today,
        )
    with pytest.raises(ValueError, match="cannot exceed"):
        BackfillDateRange(
            start=today - timedelta(days=MAX_BACKFILL_DAYS + 1),
            end=today,
        )
    with pytest.raises(ValueError, match="later than today's"):
        resolve_backfill_date_range(
            days=1,
            start=None,
            end=today + timedelta(days=1),
            today=today,
        )


def test_cli_source_flags_are_allowlisted_deduplicated_and_stably_ordered() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--days",
            "2",
            "--source",
            BackfillSource.NATIONAL_CARBON.value,
            "--source",
            BackfillSource.DEMAND.value,
            "--source",
            BackfillSource.DEMAND.value,
            "--dry-run",
        ]
    )

    request = request_from_args(args, today=date(2026, 7, 12))

    assert request.sources == (
        BackfillSource.DEMAND,
        BackfillSource.NATIONAL_CARBON,
    )
    assert request.dry_run is True
    with pytest.raises(SystemExit):
        parser.parse_args(["--days", "2", "--source", "not-a-source"])


def test_dry_run_performs_no_http_lock_or_repository_operation() -> None:
    specs, adapters = runner_sources()
    repository = FakeRepository()
    locks = FakeLocks()
    runner = HistoryBackfillRunner(
        sources=specs,
        repository=repository,
        locks=locks,
        clock=lambda: NOW,
    )
    request = BackfillRequest(
        date_range=BackfillDateRange(
            start=date(2026, 7, 10),
            end=date(2026, 7, 12),
        ),
        dry_run=True,
    )

    report = asyncio.run(runner.run(request))

    assert len(report.outcomes) == len(SOURCE_ORDER) * 2
    assert {outcome.status for outcome in report.outcomes} == {BackfillStatus.PLANNED}
    assert all(adapter.windows == [] for adapter in adapters.values())
    assert repository.successes == []
    assert repository.failures == []
    assert locks.names == []
    assert report.exit_code == 0


def test_chunk_failure_is_checkpointed_and_does_not_stop_any_source() -> None:
    specs, adapters = runner_sources(
        failing_source=BackfillSource.GENERATION,
        failing_calls={0},
    )
    repository = FakeRepository()
    locks = FakeLocks()
    runner = HistoryBackfillRunner(
        sources=specs,
        repository=repository,
        locks=locks,
        clock=lambda: NOW,
    )
    request = BackfillRequest(
        date_range=BackfillDateRange(
            start=date(2026, 7, 10),
            end=date(2026, 7, 12),
        )
    )

    report = asyncio.run(runner.run(request))

    assert len(report.outcomes) == len(SOURCE_ORDER) * 2
    assert report.outcomes[0].status is BackfillStatus.FAILED
    assert report.outcomes[0].failure_checkpoint_recorded is True
    assert report.outcomes[1].status is BackfillStatus.SUCCEEDED
    assert len(adapters[BackfillSource.GENERATION].windows) == 2
    assert all(len(adapter.windows) == 2 for adapter in adapters.values())
    assert len(repository.successes) == len(SOURCE_ORDER) * 2 - 1
    assert len(repository.failures) == 1
    failure = repository.failures[0]
    assert failure["job_id"].startswith("history-backfill-v1:")
    assert failure["job_id"] != BackfillSource.GENERATION.value
    assert "do-not-print" not in failure["error_message"]
    assert "[redacted]" in failure["error_message"]
    assert "https://" not in failure["error_message"]
    assert report.exit_code == 1

    rendered = render_report(report)
    assert "must-not-be-printed" not in rendered
    assert "do-not-print" not in rendered
    assert "https://" not in rendered
    assert "elexon.freq" in rendered
    assert "estimates only" in rendered
    assert "failure_types=RuntimeError:1" in rendered
    assert "status: incomplete" in rendered


def test_forecast_and_notice_backfill_bounds_publication_not_validity() -> None:
    window = ObservationWindow(
        start=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
        end=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
    )
    forecast = DemandForecastRecord(
        source_key="ndf:inside-publication",
        forecast_for=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
        published_at=datetime(2026, 7, 11, 1, 0, tzinfo=UTC),
        retrieved_at=NOW,
        demand_mw=30_000,
        classification=DataClassification.FORECAST,
    )
    notice = RemitUnavailabilityRecord(
        source_key="remit:inside-publication",
        mrid="mrid-1",
        revision_number=2,
        message_id=10,
        published_at=datetime(2026, 7, 11, 2, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 11, 1, 59, tzinfo=UTC),
        retrieved_at=NOW,
        event_start=datetime(2026, 8, 1, tzinfo=UTC),
        event_end=datetime(2026, 8, 2, tzinfo=UTC),
    )
    upper_boundary = DemandForecastRecord(
        source_key="ndf:upper-boundary",
        forecast_for=datetime(2026, 7, 12, 12, 0, tzinfo=UTC),
        published_at=window.end,
        retrieved_at=NOW,
        demand_mw=31_000,
    )
    result = AdapterResult(
        source_id="elexon.mixed-test",
        dataset="TEST",
        endpoint="mock/history",
        window=window,
        retrieved_at=NOW,
        request_url="https://example.test/mock/history",
        records=(forecast, notice, upper_boundary),
        raw_payload={"data": []},
        raw_body=b'{"data":[]}',
        checksum_sha256="b" * 64,
    )

    bounded = _retain_records_in_window(result)

    assert bounded.records == (forecast, notice)
    assert forecast.forecast_for > window.end
    assert notice.event_start > window.end
    assert any("outside the requested window" in item for item in bounded.warnings)


def test_observed_source_query_overlap_keeps_persisted_window_bounded() -> None:
    specs, adapters = runner_sources()
    generation = adapters[BackfillSource.GENERATION]
    specs = tuple(
        BackfillSourceSpec(
            source=spec.source,
            adapter=spec.adapter,
            chunk_days=spec.chunk_days,
            lock_source_id=spec.lock_source_id,
            query_end_overlap=(
                timedelta(minutes=15)
                if spec.source is BackfillSource.GENERATION
                else timedelta(0)
            ),
        )
        for spec in specs
    )
    repository = FakeRepository()
    runner = HistoryBackfillRunner(
        sources=specs,
        repository=repository,
        locks=FakeLocks(),
        clock=lambda: NOW,
    )
    date_range = BackfillDateRange(
        start=date(2026, 7, 11),
        end=date(2026, 7, 12),
    )
    target = plan_settlement_chunks(date_range, chunk_days=1)[0]

    report = asyncio.run(
        runner.run(
            BackfillRequest(
                date_range=date_range,
                sources=(BackfillSource.GENERATION,),
            )
        )
    )

    assert report.exit_code == 0
    assert generation.windows[0].start == target.start
    assert generation.windows[0].end == target.end + timedelta(minutes=15)
    persisted = repository.successes[0][1]
    assert persisted.window == target
    assert persisted.metadata["backfillQueryWindowEnd"] == generation.windows[0].end.isoformat()


def test_production_source_policy_includes_bounded_m2_publication_sources() -> None:
    async def scenario() -> tuple[BackfillSourceSpec, ...]:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(500, request=request)
        )
        elexon = AsyncJSONClient(transport=transport)
        carbon = AsyncJSONClient(transport=transport)
        try:
            return build_backfill_sources(
                elexon_client=elexon,
                carbon_client=carbon,
            )
        finally:
            await elexon.aclose()
            await carbon.aclose()

    specs = {spec.source: spec for spec in asyncio.run(scenario())}

    assert tuple(specs) == SOURCE_ORDER
    assert specs[BackfillSource.DEMAND_FORECASTS].chunk_days == 1
    assert specs[BackfillSource.WIND_FORECASTS].chunk_days == 7
    assert specs[BackfillSource.REMIT_REVISIONS].chunk_days == 1
    assert specs[BackfillSource.SYSTEM_WARNINGS].chunk_days == 7
    assert specs[BackfillSource.NATIONAL_CARBON].split_at_year_boundary is True
    assert specs[BackfillSource.GENERATION].query_end_overlap == timedelta(minutes=15)
    assert specs[BackfillSource.DEMAND].query_end_overlap == timedelta(hours=1)
    assert all(spec.chunk_days <= 7 for spec in specs.values())


def test_completed_chunk_checkpoint_is_skipped_unless_forced() -> None:
    specs, adapters = runner_sources()
    repository = FakeRepository()
    locks = FakeLocks()
    date_range = BackfillDateRange(
        start=date(2026, 7, 11),
        end=date(2026, 7, 12),
    )
    window = plan_settlement_chunks(date_range, chunk_days=1)[0]
    job_id = backfill_job_id(BackfillSource.GENERATION, window)
    repository.checkpoints[job_id] = IngestionCheckpoint(
        job_id=job_id,
        last_attempted_at=NOW,
        last_succeeded_at=NOW,
        window_end=window.end,
    )
    runner = HistoryBackfillRunner(
        sources=specs,
        repository=repository,
        locks=locks,
        clock=lambda: NOW,
    )

    resumed = asyncio.run(
        runner.run(
            BackfillRequest(
                date_range=date_range,
                sources=(BackfillSource.GENERATION,),
            )
        )
    )
    forced = asyncio.run(
        runner.run(
            BackfillRequest(
                date_range=date_range,
                sources=(BackfillSource.GENERATION,),
                force=True,
            )
        )
    )

    assert resumed.outcomes[0].status is BackfillStatus.SKIPPED_COMPLETED
    assert forced.outcomes[0].status is BackfillStatus.SUCCEEDED
    assert len(adapters[BackfillSource.GENERATION].windows) == 1


def test_lock_names_match_live_ingestion_namespace_and_locked_is_nonzero() -> None:
    specs, _ = runner_sources()
    blocked_name = f"50hz:ingest:{BackfillSource.DEMAND.value}"
    locks = FakeLocks(unavailable={blocked_name})
    runner = HistoryBackfillRunner(
        sources=specs,
        repository=FakeRepository(),
        locks=locks,
        clock=lambda: NOW,
    )

    report = asyncio.run(
        runner.run(
            BackfillRequest(
                date_range=BackfillDateRange(
                    start=date(2026, 7, 11),
                    end=date(2026, 7, 12),
                ),
                sources=(BackfillSource.DEMAND,),
            )
        )
    )

    assert locks.names == [blocked_name]
    assert report.outcomes[0].status is BackfillStatus.SKIPPED_LOCKED
    assert report.exit_code == 1


def test_request_rejects_values_outside_source_allowlist() -> None:
    with pytest.raises(TypeError, match="allow-list"):
        BackfillRequest(
            date_range=BackfillDateRange(
                start=date(2026, 7, 11),
                end=date(2026, 7, 12),
            ),
            sources=("elexon.unknown",),  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="carbon history is unavailable"):
        BackfillRequest(
            date_range=BackfillDateRange(
                start=NATIONAL_CARBON_EARLIEST_DATE - timedelta(days=1),
                end=NATIONAL_CARBON_EARLIEST_DATE,
            ),
            sources=(BackfillSource.NATIONAL_CARBON,),
        )

    # Elexon-only ranges are not incorrectly constrained by NESO availability.
    BackfillRequest(
        date_range=BackfillDateRange(
            start=NATIONAL_CARBON_EARLIEST_DATE - timedelta(days=1),
            end=NATIONAL_CARBON_EARLIEST_DATE,
        ),
        sources=(BackfillSource.DEMAND,),
    )


def test_missing_range_is_a_validation_error_not_an_implicit_backfill() -> None:
    args = argparse.Namespace(
        days=None,
        start=None,
        end=None,
        source=None,
        dry_run=False,
        force=False,
    )
    with pytest.raises(ValueError, match="provide --days"):
        request_from_args(args, today=date(2026, 7, 12))


def test_cli_requires_database_url_even_for_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def missing_url() -> str:
        raise DatabaseNotConfiguredError("secret setup detail")

    monkeypatch.setattr(backfill_module, "configured_database_url", missing_url)

    exit_code = main(["--days", "1", "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "DATABASE_URL is required for history backfill\n"
    assert "secret setup detail" not in captured.err


def test_cli_never_prints_setup_exception_messages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_without_leaking(_: BackfillRequest):
        raise RuntimeError(
            "postgresql://user:password@host/db token=secret full payload follows"
        )

    monkeypatch.setattr(
        backfill_module,
        "configured_database_url",
        lambda: "postgresql+asyncpg://configured",
    )
    monkeypatch.setattr(backfill_module, "run_configured_backfill", fail_without_leaking)

    exit_code = main(["--days", "1"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "RuntimeError" in captured.err
    assert "password" not in captured.err
    assert "secret" not in captured.err
    assert "payload" not in captured.err
