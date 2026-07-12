from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.db.models import (
    CarbonObservation,
    DemandObservation,
    GenerationObservation,
    InterconnectorObservation,
)
from app.domain.enums import FactQuality
from app.history.repository import (
    FUELINST_SOURCE_ID,
    INDO_SOURCE_ID,
    MAX_HISTORY_WINDOW,
    NATIONAL_CARBON_SOURCE_ID,
    HistoryMetric,
    HistorySeriesRequest,
    NormalizedHistoryRepository,
)
from app.persistence.records import source_metadata_values


START = datetime(2026, 4, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


class FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._rows)


class FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[Any] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "FakeSession":
        self.entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def execute(self, statement: Any) -> FakeResult:
        self.executed.append(statement)
        return FakeResult(self.rows)


def common(
    observed_at: datetime,
    *,
    source_id: str,
    revision: int = 0,
    source_record_id: str | None = None,
    quality: FactQuality = FactQuality.VALIDATED,
    row_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id or uuid.uuid4(),
        "source_id": source_id,
        "raw_payload_id": None,
        "source_record_id": source_record_id,
        "observed_at": observed_at,
        "published_at": observed_at + timedelta(minutes=1),
        "retrieved_at": observed_at + timedelta(minutes=2),
        "revision": revision,
        "quality": quality,
        "attributes": {},
    }


def carbon(
    observed_at: datetime,
    value: float,
    *,
    revision: int = 0,
    source_id: str = NATIONAL_CARBON_SOURCE_ID,
    region_code: str = "GB",
    quality: FactQuality = FactQuality.ESTIMATED,
    source_record_id: str | None = "neso:carbon:record",
    row_id: uuid.UUID | None = None,
) -> CarbonObservation:
    return CarbonObservation(
        **common(
            observed_at,
            source_id=source_id,
            revision=revision,
            source_record_id=source_record_id,
            quality=quality,
            row_id=row_id,
        ),
        region_code=region_code,
        intensity_gco2_kwh=value,
        index_label="low",
        generation_mix=[],
    )


def demand(
    observed_at: datetime,
    value: float,
    *,
    revision: int = 0,
    source_id: str = INDO_SOURCE_ID,
    series_key: str = "gb",
    demand_type: str = "indo",
    source_record_id: str | None = "elexon:INDO:record",
) -> DemandObservation:
    return DemandObservation(
        **common(
            observed_at,
            source_id=source_id,
            revision=revision,
            source_record_id=source_record_id,
        ),
        series_key=series_key,
        demand_type=demand_type,
        demand_mw=value,
        settlement_date=None,
        settlement_period=None,
    )


def generation(
    observed_at: datetime,
    value: float,
    *,
    revision: int = 0,
    source_id: str = FUELINST_SOURCE_ID,
    series_key: str = "WIND",
    fuel_type: str = "wind",
    source_record_id: str | None = "elexon:FUELINST:record:WIND",
) -> GenerationObservation:
    return GenerationObservation(
        **common(
            observed_at,
            source_id=source_id,
            revision=revision,
            source_record_id=source_record_id,
        ),
        series_key=series_key,
        fuel_type=fuel_type,
        asset_id=None,
        generation_mw=value,
        settlement_date=None,
        settlement_period=None,
    )


def interconnector(
    observed_at: datetime,
    value: float,
    *,
    revision: int = 0,
    source_id: str = FUELINST_SOURCE_ID,
    connector_code: str = "INTFR",
    source_record_id: str | None = "elexon:interconnector:record:INTFR",
) -> InterconnectorObservation:
    return InterconnectorObservation(
        **common(
            observed_at,
            source_id=source_id,
            revision=revision,
            source_record_id=source_record_id,
        ),
        connector_code=connector_code,
        asset_id=None,
        counterparty="France",
        flow_mw=value,
    )


def request(
    metric_id: HistoryMetric,
    *,
    source_id: str,
    selector: str | None = None,
    start: datetime = START,
    end: datetime = END,
) -> HistorySeriesRequest:
    return HistorySeriesRequest(
        metric_id=metric_id,
        source_id=source_id,
        selector=selector,
        start=start,
        end=end,
    )


def run_load(
    history_request: HistorySeriesRequest,
    rows: list[Any],
) -> tuple[Any, FakeSession]:
    session = FakeSession(rows)
    repository = NormalizedHistoryRepository(lambda: session)
    return asyncio.run(repository.load(history_request)), session


def assert_bounded_revision_preserving_query(
    statement: Any,
    *,
    table_name: str,
    expected_params: set[Any],
) -> None:
    sql = str(statement)
    params: list[Any] = []
    for value in statement.compile().params.values():
        if isinstance(value, (list, tuple, set, frozenset)):
            params.extend(value)
        else:
            params.append(value)
    assert f"{table_name}.observed_at >=" in sql
    assert f"{table_name}.observed_at <" in sql
    assert f"{table_name}.source_id =" in sql
    assert all(expected in params for expected in expected_params)
    assert (
        f"ORDER BY {table_name}.observed_at ASC, "
        f"{table_name}.revision ASC, {table_name}.id ASC"
    ) in sql
    assert "row_number" not in sql.casefold()
    assert "distinct" not in sql.casefold()


def test_repository_source_allowlist_matches_ingestion_canonicalization() -> None:
    national = source_metadata_values(
        provider="neso.carbon.national.current",
        dataset="carbon_intensity_national",
        request_url="https://api.carbonintensity.org.uk/intensity",
    )
    interconnector = source_metadata_values(
        provider="elexon.interconnectors",
        dataset="FUELINST",
        request_url="https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST",
    )
    generation_source = source_metadata_values(
        provider="elexon.fuelinst",
        dataset="FUELINST",
        request_url="https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST",
    )

    assert national["id"] == NATIONAL_CARBON_SOURCE_ID
    assert national["id"] == "neso.carbon-intensity-national"
    assert interconnector["id"] == FUELINST_SOURCE_ID
    assert generation_source["id"] == FUELINST_SOURCE_ID
    assert FUELINST_SOURCE_ID == "elexon.fuelinst"


def test_loads_national_estimated_carbon_with_exact_identity_and_filters() -> None:
    first = carbon(START, 85, revision=0, source_record_id="carbon-old")
    revision = carbon(START, 82, revision=2, source_record_id="carbon-new")
    result, session = run_load(
        request(
            HistoryMetric.NATIONAL_CARBON,
            source_id=NATIONAL_CARBON_SOURCE_ID,
        ),
        [first, revision],
    )

    assert result.identity.metric_id == "carbon.intensity.national"
    assert result.identity.geography == "GB"
    assert result.identity.unit == "gCO2/kWh"
    assert result.identity.fact_class == "estimated"
    assert result.identity.source_id == NATIONAL_CARBON_SOURCE_ID
    assert result.identity.methodology_version == "neso-national-carbon-v1"
    assert result.source_cadence_minutes == 30
    assert [item.value for item in result.observations] == [85, 82]
    assert [item.revision for item in result.observations] == [0, 2]
    assert [item.source_record_id for item in result.observations] == [
        "carbon-old",
        "carbon-new",
    ]
    assert session.entered is True and session.exited is True
    statement = session.executed[0]
    assert_bounded_revision_preserving_query(
        statement,
        table_name="carbon_observations",
        expected_params={
            START,
            END,
            NATIONAL_CARBON_SOURCE_ID,
            "GB",
            FactQuality.ESTIMATED,
        },
    )
    assert "carbon_observations.region_code =" in str(statement)
    assert "carbon_observations.quality =" in str(statement)


def test_loads_only_indo_national_demand_as_observed_half_hours() -> None:
    row = demand(START + timedelta(minutes=30), 27_500)
    result, session = run_load(
        request(
            HistoryMetric.NATIONAL_DEMAND,
            source_id=INDO_SOURCE_ID,
        ),
        [row],
    )

    assert result.identity.metric_id == "demand.national_outturn"
    assert result.identity.geography == "GB"
    assert result.identity.unit == "MW"
    assert result.identity.fact_class == "observed"
    assert result.identity.methodology_version == "indo-national-demand-v1"
    assert result.source_cadence_minutes == 30
    assert result.observations[0].timestamp == START + timedelta(minutes=30)
    assert result.observations[0].value == 27_500
    statement = session.executed[0]
    assert_bounded_revision_preserving_query(
        statement,
        table_name="demand_observations",
        expected_params={START, END, INDO_SOURCE_ID, "gb", "indo"},
    )
    assert "demand_observations.series_key =" in str(statement)
    assert "demand_observations.demand_type =" in str(statement)


def test_loads_one_generation_code_and_retains_every_db_revision() -> None:
    old = generation(START, 4_000, revision=0, source_record_id="wind-r0")
    latest = generation(START, 4_100, revision=3, source_record_id="wind-r3")
    result, session = run_load(
        request(
            HistoryMetric.GENERATION_FUEL,
            source_id=FUELINST_SOURCE_ID,
            selector=" wind ",
        ),
        [old, latest],
    )

    assert result.identity.metric_id == (
        "generation.transmission_visible_by_fuel.wind"
    )
    assert result.identity.fact_class == "observed"
    assert result.identity.methodology_version == "fuelinst-generation-v1"
    assert result.source_cadence_minutes == 5
    assert [item.revision for item in result.observations] == [0, 3]
    assert [item.value for item in result.observations] == [4_000, 4_100]
    statement = session.executed[0]
    assert_bounded_revision_preserving_query(
        statement,
        table_name="generation_observations",
        expected_params={START, END, FUELINST_SOURCE_ID, "WIND", "wind"},
    )
    assert "generation_observations.series_key =" in str(statement)
    assert "generation_observations.fuel_type =" in str(statement)


def test_loads_one_signed_interconnector_without_changing_export_sign() -> None:
    row = interconnector(START + timedelta(minutes=5), -725)
    result, session = run_load(
        request(
            HistoryMetric.INTERCONNECTOR_FLOW,
            source_id=FUELINST_SOURCE_ID,
            selector="intfr",
        ),
        [row],
    )

    assert result.identity.metric_id == "interconnector.flow.intfr"
    assert result.identity.unit == "MW"
    assert result.identity.fact_class == "observed"
    assert result.identity.methodology_version == (
        "fuelinst-interconnector-flow-v1"
    )
    assert result.source_cadence_minutes == 5
    assert result.observations[0].value == -725
    statement = session.executed[0]
    assert_bounded_revision_preserving_query(
        statement,
        table_name="interconnector_observations",
        expected_params={START, END, FUELINST_SOURCE_ID, "INTFR"},
    )
    assert "interconnector_observations.connector_code =" in str(statement)


def test_source_record_falls_back_to_stable_table_row_provenance() -> None:
    row_id = uuid.UUID("42b0baaf-c1db-4c12-b153-8ae6cb15d215")
    row = carbon(
        START,
        80,
        source_record_id=None,
        row_id=row_id,
    )
    result, _ = run_load(
        request(
            HistoryMetric.NATIONAL_CARBON,
            source_id=NATIONAL_CARBON_SOURCE_ID,
        ),
        [row],
    )

    assert result.observations[0].source_record_id == (
        f"carbon_observations:row:{row_id}"
    )


def test_exactly_95_days_is_allowed_and_remains_bounded() -> None:
    history_request = request(
        HistoryMetric.NATIONAL_DEMAND,
        source_id=INDO_SOURCE_ID,
        end=START + MAX_HISTORY_WINDOW,
    )

    result, session = run_load(history_request, [])

    assert result.observations == ()
    assert len(session.executed) == 1
    assert START + MAX_HISTORY_WINDOW in (
        session.executed[0].compile().params.values()
    )


def test_window_over_95_days_is_rejected_before_any_query() -> None:
    with pytest.raises(ValidationError, match="cannot exceed 95 days"):
        request(
            HistoryMetric.NATIONAL_DEMAND,
            source_id=INDO_SOURCE_ID,
            end=START + MAX_HISTORY_WINDOW + timedelta(minutes=30),
        )


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (START.replace(tzinfo=None), END, "timezone"),
        (START, END.replace(tzinfo=None), "timezone"),
        (START + timedelta(minutes=5), END, "exact UTC half-hour"),
        (START, END + timedelta(seconds=1), "exact UTC half-hour"),
        (START, START, "after start"),
        (END, START, "after start"),
    ],
)
def test_request_rejects_naive_misaligned_or_unordered_bounds(
    start: datetime,
    end: datetime,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        request(
            HistoryMetric.NATIONAL_DEMAND,
            source_id=INDO_SOURCE_ID,
            start=start,
            end=end,
        )


def test_request_cannot_be_unbounded() -> None:
    with pytest.raises(ValidationError):
        HistorySeriesRequest.model_validate(
            {
                "metric_id": "demand.national_outturn",
                "source_id": INDO_SOURCE_ID,
                "start": START,
            }
        )


def test_unknown_metric_is_rejected_by_explicit_allowlist() -> None:
    with pytest.raises(ValidationError):
        HistorySeriesRequest(
            metric_id="frequency.system",  # type: ignore[arg-type]
            source_id="elexon.freq",
            start=START,
            end=END,
        )


@pytest.mark.parametrize(
    ("metric_id", "source_id", "selector"),
    [
        (HistoryMetric.NATIONAL_CARBON, "neso.carbon", None),
        (HistoryMetric.NATIONAL_DEMAND, FUELINST_SOURCE_ID, None),
        (HistoryMetric.GENERATION_FUEL, INDO_SOURCE_ID, "WIND"),
        (HistoryMetric.INTERCONNECTOR_FLOW, INDO_SOURCE_ID, "INTFR"),
    ],
)
def test_metric_source_pair_is_fixed_and_cannot_be_mixed(
    metric_id: HistoryMetric,
    source_id: str,
    selector: str | None,
) -> None:
    with pytest.raises(ValidationError, match="requires source_id"):
        request(metric_id, source_id=source_id, selector=selector)


@pytest.mark.parametrize(
    ("metric_id", "source_id"),
    [
        (HistoryMetric.GENERATION_FUEL, FUELINST_SOURCE_ID),
        (HistoryMetric.INTERCONNECTOR_FLOW, FUELINST_SOURCE_ID),
    ],
)
def test_selector_is_required_for_multi_series_tables(
    metric_id: HistoryMetric,
    source_id: str,
) -> None:
    with pytest.raises(ValidationError, match="requires a selector"):
        request(metric_id, source_id=source_id)


@pytest.mark.parametrize(
    ("metric_id", "source_id", "selector", "message"),
    [
        (
            HistoryMetric.GENERATION_FUEL,
            FUELINST_SOURCE_ID,
            "MADE_UP_FUEL",
            "unknown FUELINST generation",
        ),
        (
            HistoryMetric.INTERCONNECTOR_FLOW,
            FUELINST_SOURCE_ID,
            "INTUNKNOWN",
            "unknown FUELINST interconnector",
        ),
    ],
)
def test_unknown_selectors_are_rejected(
    metric_id: HistoryMetric,
    source_id: str,
    selector: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        request(metric_id, source_id=source_id, selector=selector)


def test_selector_is_forbidden_for_single_series_metrics() -> None:
    with pytest.raises(ValidationError, match="does not accept a selector"):
        request(
            HistoryMetric.NATIONAL_CARBON,
            source_id=NATIONAL_CARBON_SOURCE_ID,
            selector="GB",
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_database_values_are_rejected(value: float) -> None:
    row = demand(START, value)
    repository = NormalizedHistoryRepository(lambda: FakeSession([row]))

    with pytest.raises(ValueError, match="must be finite"):
        asyncio.run(
            repository.load(
                request(
                    HistoryMetric.NATIONAL_DEMAND,
                    source_id=INDO_SOURCE_ID,
                )
            )
        )


def test_fake_session_cannot_smuggle_a_different_source_into_series() -> None:
    row = demand(START, 20_000, source_id="some.other.indo")

    with pytest.raises(ValueError, match="different source"):
        run_load(
            request(
                HistoryMetric.NATIONAL_DEMAND,
                source_id=INDO_SOURCE_ID,
            ),
            [row],
        )


def test_fake_session_cannot_smuggle_a_different_selector_into_series() -> None:
    row = generation(
        START,
        1_000,
        series_key="CCGT",
        fuel_type="gas",
    )

    with pytest.raises(ValueError, match="incompatible generation"):
        run_load(
            request(
                HistoryMetric.GENERATION_FUEL,
                source_id=FUELINST_SOURCE_ID,
                selector="WIND",
            ),
            [row],
        )


def test_fake_session_cannot_smuggle_observed_quality_into_estimated_carbon() -> None:
    row = carbon(START, 80, quality=FactQuality.VALIDATED)

    with pytest.raises(ValueError, match="incompatible national carbon"):
        run_load(
            request(
                HistoryMetric.NATIONAL_CARBON,
                source_id=NATIONAL_CARBON_SOURCE_ID,
            ),
            [row],
        )


def test_fake_session_cannot_smuggle_a_row_outside_query_bounds() -> None:
    row = demand(END, 20_000)

    with pytest.raises(ValueError, match="outside the requested bounds"):
        run_load(
            request(
                HistoryMetric.NATIONAL_DEMAND,
                source_id=INDO_SOURCE_ID,
            ),
            [row],
        )


def test_fake_session_cannot_return_a_different_observation_table() -> None:
    row = carbon(START, 80)

    with pytest.raises(TypeError, match="unexpected CarbonObservation"):
        run_load(
            request(
                HistoryMetric.NATIONAL_DEMAND,
                source_id=INDO_SOURCE_ID,
            ),
            [row],
        )


def test_row_requires_source_record_or_stable_database_id() -> None:
    row = carbon(START, 80, source_record_id=None)
    row.id = None

    with pytest.raises(ValueError, match="stable row provenance"):
        run_load(
            request(
                HistoryMetric.NATIONAL_CARBON,
                source_id=NATIONAL_CARBON_SOURCE_ID,
            ),
            [row],
        )


def test_repository_requires_session_factory_and_request_contract() -> None:
    with pytest.raises(TypeError, match="session_factory"):
        NormalizedHistoryRepository(None)  # type: ignore[arg-type]

    repository = NormalizedHistoryRepository(lambda: FakeSession())
    with pytest.raises(TypeError, match="HistorySeriesRequest"):
        asyncio.run(repository.load(None))  # type: ignore[arg-type]
