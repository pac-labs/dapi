"""Exercise the smoke scenario when container restart changes its published port."""

import io
import itertools
import json
from contextlib import nullcontext
from types import SimpleNamespace
import urllib.error

from scripts import container_smoke


def test_restart_refreshes_ephemeral_container_port(tmp_path, monkeypatch):
    state = {"restarted": False, "port_reads": 0, "record": None}
    after_restart = []

    def run(arguments, **kwargs):
        operation = arguments[1]
        if operation == "restart":
            state["restarted"] = True
        if operation == "port":
            state["port_reads"] += 1
            port = 18081 if state["restarted"] else 18080
            return SimpleNamespace(stdout=f"127.0.0.1:{port}\n")
        return SimpleNamespace(stdout="")

    class Response(io.BytesIO):
        status = 200

    def urlopen(request, timeout):
        if state["restarted"]:
            after_restart.append(request.full_url)
            if not request.full_url.startswith("http://127.0.0.1:18081/"):
                raise urllib.error.URLError("old port is closed")
        sources = tmp_path / "config/sources.yaml"
        if sources.exists() and "components: []" in sources.read_text():
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {},
                                         io.BytesIO(b'{"error":"component not found"}'))
        if request.get_method() == "POST":
            state["record"] = {"id": 1, **json.loads(request.data)}
            response = Response(json.dumps(state["record"]).encode())
            response.status = 201
            return response
        result = state["record"] if request.full_url.endswith("/1") else []
        return Response(json.dumps(result).encode())

    clock = itertools.count()
    monkeypatch.setattr(container_smoke.sys, "argv", ["container_smoke.py", "docker", "test-image"])
    monkeypatch.setattr(container_smoke.tempfile, "TemporaryDirectory", lambda: nullcontext(str(tmp_path)))
    monkeypatch.setattr(container_smoke.subprocess, "run", run)
    monkeypatch.setattr(container_smoke.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(container_smoke.time, "sleep", lambda *_: None)
    monkeypatch.setattr(container_smoke.time, "monotonic", lambda: next(clock))
    container_smoke.main()
    assert state["port_reads"] == 2
    assert after_restart and all(url.startswith("http://127.0.0.1:18081/") for url in after_restart)
