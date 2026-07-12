from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("filename", "start_command", "cron_schedule"),
    [
        (
            "railway.history.json",
            "50hz-history-materialize --refresh-latest",
            "17 4,10 * * *",
        ),
        (
            "railway.forecast.json",
            "50hz-forecast-verify --refresh-latest",
            "17 11 * * *",
        ),
    ],
)
def test_railway_cron_is_bounded_and_has_no_web_healthcheck(
    filename: str,
    start_command: str,
    cron_schedule: str,
) -> None:
    config = json.loads((ROOT / filename).read_text(encoding="utf-8"))

    assert config["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile",
    }
    assert config["deploy"] == {
        "startCommand": start_command,
        "preDeployCommand": None,
        "healthcheckPath": None,
        "healthcheckTimeout": None,
        "restartPolicyType": "NEVER",
        "restartPolicyMaxRetries": None,
        "cronSchedule": cron_schedule,
    }
