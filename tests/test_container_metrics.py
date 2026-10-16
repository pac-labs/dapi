"""Container metrics never widen host access or expose arbitrary snapshot fields."""

import importlib.util
import json
import time
from pathlib import Path

from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.container_metrics import read
from test_http import ADMIN

spec = importlib.util.spec_from_file_location("metrics_collector", Path(__file__).parents[1] / "deploy/metrics/collect.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_sampler_allowlist_cpu_delta_and_missing_memory():
    calls = []
    def runner(arguments):
        calls.append(arguments)
        if arguments[0] == "container":
            return "running|2026-10-15T00:00:00Z|2"
        return json.dumps([{"name": name, "cpu_time": "1m10s", "mem_usage": "0B / 4GB",
            "net_io": "1KiB / 2MB", "block_io": "3B / 0B", "pids": "4", "secret": "discard"}
            for name in collector.NAMES])
    previous = {"collectedAt": 100, "containers": [{"name": name, "cpuTotalSeconds": 65,
        "startedAt": "2026-10-15T00:00:00Z"} for name in collector.NAMES]}
    snapshot = collector.collect(previous, runner, memory_supported=False, now=110)
    assert len(calls) == len(collector.NAMES) + 1
    assert all(call[-1] in collector.NAMES for call in calls[:-1])
    assert calls[-1][-len(collector.NAMES):] == list(collector.NAMES)
    for row in snapshot["containers"]:
        assert row["cpuPercent"] == 50
        assert row["memoryBytes"] is None and row["memoryLimitBytes"] is None
        assert row["networkRxBytes"] == 1024 and row["networkTxBytes"] == 2000000
    assert "secret" not in str(snapshot) and "discard" not in str(snapshot)


def test_snapshot_missing_stale_corrupt_and_allowlisted_fields(setup_project):
    settings, _, _ = setup_project
    assert read(settings)["state"] == "disabled"
    settings.container_metrics_file = settings.data.parent / "containers.json"
    assert read(settings)["state"] == "unavailable"
    snapshot = {"version": 1, "collectedAt": time.time() - 120, "memorySupported": False,
        "containers": [{"name": "looprd", "state": "running", "cpuPercent": None}]}
    settings.container_metrics_file.write_text(json.dumps(snapshot))
    assert read(settings)["state"] == "stale"
    snapshot["containers"][0]["environment"] = "private-secret"
    settings.container_metrics_file.write_text(json.dumps(snapshot))
    assert read(settings) == {"state": "invalid", "containers": []}
    settings.container_metrics_file.write_bytes(b"x" * 65537)
    assert read(settings)["state"] == "invalid"
    snapshot["containers"][0].pop("environment")
    snapshot["containers"][0]["name"] = "unrelated-container"
    settings.container_metrics_file.write_text(json.dumps(snapshot))
    assert read(settings)["state"] == "invalid"


def test_metrics_endpoint_protected_and_optional(setup_project):
    settings, _, _ = setup_project
    settings.container_metrics_file = settings.data.parent / "containers.json"
    settings.container_metrics_file.write_text(json.dumps({"version": 1, "collectedAt": time.time(),
        "memorySupported": True, "containers": [{"name": "looprd", "state": "running", "memoryBytes": 1048576}]}))
    with TestClient(create_app(settings)) as client:
        assert client.get("/_looprd/ops").status_code == 401
        response = client.get("/_looprd/ops", headers=ADMIN).json()["containers"]
        assert response["state"] == "available"
        assert response["containers"][0]["memoryBytes"] == 1048576


def test_sampler_failure_never_falls_back_to_all_containers():
    calls = []
    def unavailable(arguments):
        calls.append(arguments)
        raise ValueError("private host error must not escape")
    result = collector.collect(previous={"containers": None}, runner=unavailable, memory_supported=False)
    assert len(calls) == len(collector.NAMES) and all(call[0] == "container" for call in calls)
    assert all(row["state"] == "unknown" and row["error"] == "inspect-unavailable" for row in result["containers"])
    assert "private" not in str(result)
