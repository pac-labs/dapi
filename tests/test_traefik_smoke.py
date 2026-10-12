"""The proxy smoke adapts its Host header to deployment configuration."""

import urllib.error
from pathlib import Path

import pytest

from scripts.traefik_smoke import check_proxy, router_host


def test_public_deployment_routers_use_placeholder_hostname():
    root = Path(__file__).resolve().parents[1] / "deploy/traefik"
    for filename in ("looprd.yml", "management.yml.example"):
        assert router_host((root / filename).read_text()) == "api.example.com"


@pytest.mark.parametrize("hostname", ["api.example.com", "custom.internal", "localhost"])
def test_router_host_reads_deployment_rule(hostname):
    assert router_host(f"      rule: Host(`{hostname}`) && PathPrefix(`/looprd/`)\n") == hostname


@pytest.mark.parametrize("rule", ["", "rule: PathPrefix(`/looprd/`)",
    "rule: Host(`a.example`) || Host(`b.example`)", "rule: Host(`a.example`, `b.example`)",
    "rule: Host(`a.example`)\nrule: Host(`b.example`)", "rule: Host(`https://a.example`)"])
def test_router_host_rejects_missing_or_unsupported_matchers(rule):
    with pytest.raises(ValueError):
        router_host(rule)


def test_proxy_requests_follow_configured_hostname(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    deployment = root / "deploy/traefik"
    deployment.mkdir(parents=True)
    (deployment / "looprd.yml").write_text(
        "http:\n  routers:\n    looprd:\n      rule: Host(`custom.internal`) && PathPrefix(`/looprd/`)\n"
        "      tls: {}\n  servers:\n    - url: http://127.0.0.1:8080\n",
    )
    requests = []

    def command(*args):
        return "127.0.0.1:18080" if args[0] == "port" else ""

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def urlopen(request, timeout):
        host = request.get_header("Host")
        requests.append(host)
        allowed = ("/looprd/roboscope/v1/scans", "/dapi/roboscope/v1/scans")
        if host == "custom.internal" and any(request.full_url.endswith(path) for path in allowed):
            return Response()
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    monkeypatch.setattr("scripts.traefik_smoke.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("scripts.traefik_smoke.subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr("scripts.traefik_smoke.smoke_files", lambda *args: None)
    check_proxy("podman", command, "test-looprd", tmp_path, root)
    assert set(requests) == {"custom.internal", "wrong.custom.internal"}
    content = (tmp_path / "dynamic/looprd.yml").read_text()
    assert "Host(`custom.internal`)" in content
    assert "tls:" not in content and "http://test-looprd:8080" in content
