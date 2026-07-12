from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.dependencies import get_grid_read_repository, get_regional_carbon_provider
from app.main import app
from app.persistence.reads import CarbonRead, ForecastRead, SourceMetadataRead
from app.regions import RegionalCarbonReading
from app.regions.service import _current_record
from app.sources.exceptions import SourceUnavailableError
from app.sources.types import CarbonIntensityRecord, DataClassification


def ceil_half_hour(value: datetime) -> datetime:
    floor = value.replace(minute=30 if value.minute >= 30 else 0, second=0, microsecond=0)
    return floor if floor == value else floor + timedelta(minutes=30)


class RegionRepository:
    def __init__(
        self,
        *,
        stored_region: bool = False,
        matching_national: bool = True,
    ) -> None:
        self.stored_region = stored_region
        self.matching_national = matching_national
        self.national_actual_reads = 0

    async def list_sources(self) -> tuple[SourceMetadataRead, ...]:
        return (
            SourceMetadataRead(
                "neso.carbon.regional",
                "neso",
                "carbon_intensity_regional",
                "NESO regional carbon intensity",
                None,
                None,
                "NESO",
                1_800,
            ),
        )

    async def get_latest_regional_carbon(
        self,
        region_code: str,
        *,
        as_of: datetime | None = None,
    ) -> CarbonRead | None:
        raise AssertionError("regional current values must come from forecast storage")

    async def get_carbon_forecast(
        self,
        *,
        region_code: str,
        window_start: datetime,
        window_end: datetime,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]:
        if (
            not self.stored_region
            or region_code not in {"SW1A", "region-13"}
            or issued_before is None
        ):
            return ()
        start = issued_before.replace(
            minute=30 if issued_before.minute >= 30 else 0,
            second=0,
            microsecond=0,
        )
        captured = issued_before - timedelta(minutes=5)
        return (
            ForecastRead(
                metric_type="carbon_intensity",
                series_key=region_code,
                value=72,
                unit="gCO2/kWh",
                valid_from=start,
                valid_to=start + timedelta(minutes=30),
                issued_at=captured,
                published_at=None,
                retrieved_at=captured,
                source_id="neso.carbon.regional",
                source_record_id="regional:stored",
                model_name="neso_carbon_intensity",
                attributes={
                    "classification": "forecast",
                    "issueTimeBasis": "retrieved_at",
                    "regionName": "London",
                    "index": "low",
                },
            ),
        )

    async def get_carbon_forecast_history(
        self,
        *,
        region_code: str,
        window_start: datetime,
        window_end: datetime,
        captured_after: datetime,
        captured_before: datetime,
        issued_before: datetime | None = None,
    ) -> tuple[ForecastRead, ...]:
        assert region_code == "GB"
        assert issued_before == captured_before
        captured = captured_before - timedelta(minutes=5)
        current_start = window_start
        current_end = current_start + timedelta(minutes=30)
        future_start = max(ceil_half_hour(captured_before), current_end)
        periods: list[tuple[datetime, datetime, float, str]] = []
        if self.matching_national:
            periods.append((current_start, current_end, 92, "national:current"))
        periods.extend(
            (
                future_start + timedelta(minutes=30 * index),
                future_start + timedelta(minutes=30 * (index + 1)),
                value,
                f"national:{index}",
            )
            for index, value in enumerate([120, 40, 30, 90])
        )
        return tuple(
            ForecastRead(
                metric_type="carbon_intensity",
                series_key="GB",
                value=value,
                unit="gCO2/kWh",
                valid_from=start,
                valid_to=end,
                issued_at=captured,
                published_at=None,
                retrieved_at=captured,
                source_id="neso.carbon.national.forecast",
                source_record_id=record_id,
                model_name="neso_carbon_intensity",
                attributes={
                    "classification": "forecast",
                    "issueTimeBasis": "retrieved_at",
                },
            )
            for start, end, value, record_id in periods
            if start < window_end and end > window_start
        )

    async def get_latest_carbon(
        self,
        *,
        as_of: datetime | None = None,
        carbon_region: str = "GB",
    ) -> CarbonRead | None:
        self.national_actual_reads += 1
        raise AssertionError("regional forecast must not be compared with actual")


class RegionProvider:
    def __init__(self, *, period_lag: timedelta | None = None) -> None:
        self.calls = 0
        self.period_lag = period_lag

    async def fetch(self, postcode: str, *, as_of: datetime) -> RegionalCarbonReading:
        self.calls += 1
        period_end = as_of - self.period_lag if self.period_lag else ceil_half_hour(
            as_of
        )
        return RegionalCarbonReading(
            postcode=postcode,
            name="London",
            intensity_gco2_kwh=81,
            rating="low",
            period_start=period_end - timedelta(minutes=30),
            period_end=period_end,
            retrieved_at=as_of,
            source_id=f"neso.carbon.postcode.{postcode}",
            dataset="carbon_intensity_regional",
            classification=DataClassification.FORECAST,
        )


class UnavailableRegionProvider:
    async def fetch(self, postcode: str, *, as_of: datetime) -> RegionalCarbonReading:
        raise SourceUnavailableError("offline")


def request(
    repository: RegionRepository,
    provider: object,
    path: str,
) -> tuple[int, dict[str, object]]:
    app.dependency_overrides[get_grid_read_repository] = lambda: repository
    app.dependency_overrides[get_regional_carbon_provider] = lambda: provider
    try:
        with TestClient(app) as client:
            response = client.get(path)
    finally:
        app.dependency_overrides.clear()
    return response.status_code, response.json()


def test_postcode_route_falls_back_to_bounded_regional_provider() -> None:
    provider = RegionProvider()
    status, payload = request(RegionRepository(), provider, "/v1/regions/sw1a%201aa")
    assert status == 200
    assert payload["postcode"] == "SW1A"
    assert payload["carbonIntensity"] == 81
    assert payload["nationalCarbonIntensity"] == 92
    assert payload["regionalIsDelayed"] is False
    assert payload["chargingWindowEnd"] > payload["chargingWindowStart"]
    assert payload["source"]["id"] == "neso.carbon.postcode.SW1A"
    assert payload["regionalFactClass"] == "forecast"
    assert payload["regionalGeographyScope"] == "regional"
    assert payload["nationalFactClass"] == "forecast"
    assert payload["nationalGeographyScope"] == "national"
    assert payload["forecastIssueTimeBasis"] == (
        "source_does_not_publish_issue_time"
    )
    assert payload["forecastIssuedAt"] == payload["forecastCapturedAt"]
    assert provider.calls == 1


def test_postcode_route_prefers_recent_stored_region() -> None:
    provider = RegionProvider()
    status, payload = request(
        RegionRepository(stored_region=True),
        provider,
        "/v1/regions/SW1A",
    )
    assert status == 200
    assert payload["carbonIntensity"] == 72
    assert payload["source"]["id"] == "neso.carbon.regional"
    assert provider.calls == 0


def test_recently_ended_regional_period_is_returned_as_delayed_source_data() -> None:
    now = datetime(2026, 7, 11, 13, 49, tzinfo=UTC)
    record = CarbonIntensityRecord(
        source_key="regional:lagged",
        period_start=now - timedelta(minutes=49),
        period_end=now - timedelta(minutes=19),
        retrieved_at=now,
        intensity_g_co2_per_kwh=74,
        classification=DataClassification.FORECAST,
        index="low",
        region_name="London",
        postcode="SW1A",
    )

    assert _current_record((record,), as_of=now) == record
    assert _current_record(
        (record,),
        as_of=now + timedelta(hours=2),
    ) is None


def test_regional_selection_never_substitutes_an_estimated_actual() -> None:
    now = datetime(2026, 7, 11, 13, 15, tzinfo=UTC)
    common = {
        "period_start": now - timedelta(minutes=15),
        "period_end": now + timedelta(minutes=15),
        "retrieved_at": now,
        "index": "low",
        "region_name": "London",
        "postcode": "SW1A",
    }
    estimate = CarbonIntensityRecord(
        source_key="regional:estimated",
        intensity_g_co2_per_kwh=70,
        classification=DataClassification.ESTIMATED,
        **common,
    )
    forecast = CarbonIntensityRecord(
        source_key="regional:forecast",
        intensity_g_co2_per_kwh=82,
        classification=DataClassification.FORECAST,
        **common,
    )

    assert _current_record((estimate, forecast), as_of=now) == forecast
    assert _current_record((estimate,), as_of=now) is None


def test_region_contract_marks_a_recently_ended_period_as_delayed() -> None:
    provider = RegionProvider(period_lag=timedelta(minutes=19))

    status, payload = request(RegionRepository(), provider, "/v1/regions/SW1A")

    assert status == 200
    assert payload["regionalIsDelayed"] is True
    assert payload["regionalPeriodEnd"] < payload["forecastIssuedAt"]


def test_region_route_never_falls_back_to_national_actual_for_comparison() -> None:
    repository = RegionRepository(matching_national=False)
    provider = RegionProvider()

    status, payload = request(repository, provider, "/v1/regions/SW1A")

    assert status == 503
    assert "compatible national forecast" in str(payload["detail"])
    assert repository.national_actual_reads == 0


def test_invalid_postcode_is_422_without_upstream_lookup() -> None:
    provider = RegionProvider()
    status, _ = request(RegionRepository(), provider, "/v1/regions/central-london")
    assert status == 422
    assert provider.calls == 0


def test_upstream_failure_is_a_temporary_gateway_error() -> None:
    status, payload = request(
        RegionRepository(),
        UnavailableRegionProvider(),
        "/v1/regions/EC1A",
    )
    assert status == 502
    assert "temporarily unavailable" in str(payload["detail"])
