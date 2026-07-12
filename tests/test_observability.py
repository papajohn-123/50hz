from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from app.http_cache import ConditionalJSONMiddleware
from app.observability import RequestObservabilityMiddleware
from app.rate_limit import RateLimitMiddleware, RateLimitPolicy


class RecordHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def request_logger() -> tuple[logging.Logger, RecordHandler]:
    handler = RecordHandler()
    log = logging.Logger("test.50hz.request", level=logging.INFO)
    log.addHandler(handler)
    log.propagate = False
    return log, handler


def privacy_app(log: logging.Logger) -> FastAPI:
    application = FastAPI(version="9.8.7")

    @application.post("/v1/ask", name="ask_question")
    async def ask() -> dict[str, str]:
        return {"answer": "bounded"}

    @application.get("/v1/regions/{postcode}", name="region")
    async def region(postcode: str) -> dict[str, str]:
        return {"postcode": postcode}

    @application.get("/v1/failure", name="handled_failure")
    async def failure() -> None:
        raise HTTPException(status_code=418, detail="private exception detail")

    application.add_middleware(
        RequestObservabilityMiddleware,
        request_logger=log,
        service_role="api",
        service_version="9.8.7",
    )
    return application


def test_log_uses_route_template_and_never_captures_private_request_values() -> None:
    log, handler = request_logger()
    application = privacy_app(log)

    with TestClient(application) as client:
        response = client.post(
            "/v1/ask?search=arbitrary-query-secret",
            json={"question": "Will my private factory use more power?"},
            headers={
                "Authorization": "Bearer private-auth-secret",
                "X-Real-IP": "203.0.113.42",
                "X-Request-ID": "SW1A",
                "X-Private": "private-header-value",
            },
        )

    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert len(handler.messages) == 1
    payload = json.loads(handler.messages[0])
    assert payload == {
        **payload,
        "method": "POST",
        "requestId": request_id,
        "responseBytes": len(response.content),
        "routeName": "ask_question",
        "routeTemplate": "/v1/ask",
        "serviceRole": "api",
        "serviceVersion": "9.8.7",
        "status": 200,
    }
    serialized = handler.messages[0].casefold()
    for private in (
        "sw1a",
        "arbitrary-query-secret",
        "private factory",
        "private-auth-secret",
            "203.0.113.42",
            "private-header-value",
            "authorization",
        ):
        assert private not in serialized


def test_dynamic_postcode_and_unmatched_path_never_appear_in_logs() -> None:
    log, handler = request_logger()
    application = privacy_app(log)

    with TestClient(application) as client:
        matched = client.get("/v1/regions/SW1A?full=SW1A1AA")
        missing = client.get("/private/SW1A/arbitrary-value")

    matched_log, missing_log = map(json.loads, handler.messages)
    assert matched.status_code == 200
    assert matched_log["routeTemplate"] == "/v1/regions/{postcode}"
    assert matched_log["routeName"] == "region"
    assert "sw1a" not in handler.messages[0].casefold()
    assert missing.status_code == 404
    assert missing_log["routeTemplate"] == "__unmatched__"
    assert missing_log["routeName"] == "__unmatched__"
    assert "private" not in handler.messages[1].casefold()
    assert "sw1a" not in handler.messages[1].casefold()
    assert matched.headers["x-request-id"]
    assert missing.headers["x-request-id"]


def test_valid_request_id_is_normalized_and_invalid_values_are_replaced() -> None:
    log, handler = request_logger()
    application = privacy_app(log)
    valid = "ABCDEF0123456789ABCDEF0123456789"

    with TestClient(application) as client:
        accepted = client.post("/v1/ask", headers={"X-Request-ID": valid})
        replaced = client.post(
            "/v1/ask",
            headers={"X-Request-ID": "../../unsafe request id with spaces"},
        )

    assert accepted.headers["x-request-id"] == valid.casefold()
    assert json.loads(handler.messages[0])["requestId"] == valid.casefold()
    assert replaced.headers["x-request-id"] != "../../unsafe request id with spaces"
    assert re.fullmatch(r"[0-9a-f]{32}", replaced.headers["x-request-id"])
    assert "unsafe" not in handler.messages[1]


def test_handled_errors_have_request_id_status_and_no_exception_detail() -> None:
    log, handler = request_logger()
    application = privacy_app(log)

    with TestClient(application) as client:
        response = client.get("/v1/failure")

    payload = json.loads(handler.messages[0])
    assert response.status_code == 418
    assert response.headers["x-request-id"] == payload["requestId"]
    assert payload["status"] == 418
    assert payload["routeTemplate"] == "/v1/failure"
    assert "private exception detail" not in handler.messages[0]


def test_conditional_304_and_early_429_are_logged_once_with_safe_routes() -> None:
    log, handler = request_logger()
    application = FastAPI()

    @application.get("/v1/grid/current", name="current_grid")
    async def current() -> dict[str, int]:
        return {"demandMW": 20_000}

    @application.post("/v1/ask", name="ask")
    async def ask() -> dict[str, str]:
        return {"answer": "ok"}

    application.add_middleware(ConditionalJSONMiddleware)
    application.add_middleware(
        RateLimitMiddleware,
        policies=(RateLimitPolicy("POST", "/v1/ask", 1, 1),),
    )
    application.add_middleware(RequestObservabilityMiddleware, request_logger=log)

    with TestClient(application) as client:
        first = client.get("/v1/grid/current")
        not_modified = client.get(
            "/v1/grid/current",
            headers={"If-None-Match": first.headers["etag"]},
        )
        allowed = client.post("/v1/ask")
        limited = client.post("/v1/ask")

    records = [json.loads(item) for item in handler.messages]
    assert [item["status"] for item in records] == [200, 304, 200, 429]
    assert records[1]["responseBytes"] == 0
    assert records[1]["routeTemplate"] == "/v1/grid/current"
    assert records[3]["routeTemplate"] == "/v1/ask"
    assert not_modified.headers["x-request-id"]
    assert allowed.headers["x-request-id"]
    assert limited.headers["x-request-id"]


def test_streaming_and_gzip_remain_compatible() -> None:
    log, handler = request_logger()
    application = FastAPI()

    @application.get("/v1/stream", name="stream")
    async def stream() -> StreamingResponse:
        async def chunks():
            yield b"a" * 800
            yield b"b" * 800

        return StreamingResponse(chunks(), media_type="application/octet-stream")

    application.add_middleware(GZipMiddleware, minimum_size=100)
    application.add_middleware(RequestObservabilityMiddleware, request_logger=log)

    with TestClient(application) as client:
        response = client.get("/v1/stream")

    record = json.loads(handler.messages[0])
    assert response.status_code == 200
    assert response.content == b"a" * 800 + b"b" * 800
    assert response.headers["content-encoding"] == "gzip"
    assert record["routeTemplate"] == "/v1/stream"
    assert record["responseBytes"] > 0


def test_production_command_disables_uvicorn_raw_access_log() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()

    assert "uvicorn app.main:app" in dockerfile
    assert "--no-access-log" in dockerfile
    assert dockerfile.index("pip install --no-cache-dir --requirement") < dockerfile.index(
        "COPY app ./app"
    )


def test_production_request_logger_writes_success_records_to_stdout() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.observability import logger; logger.info('request-probe')",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        check=True,
        text=True,
    )

    assert result.stdout.strip() == "request-probe"
    assert result.stderr == ""
