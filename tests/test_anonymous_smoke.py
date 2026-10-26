"""Exercise the proxy smoke assertions against the actual ASGI dispatcher."""

import io
import urllib.error

from starlette.testclient import TestClient

from looprd.app import create_app
from scripts.anonymous_smoke import check_anonymous


def test_proxy_smoke_assertions_use_no_credentials(setup_project, monkeypatch):
    from pathlib import Path
    import yaml
    settings, write, sources = setup_project
    root = Path(__file__).resolve().parents[1]
    write(yaml.safe_load((root / "examples/anonymous-read.yaml").read_text(encoding="utf-8")))
    sources([{"id": "public-read", "specification": "looprd.yaml"}])
    methods = []
    with TestClient(create_app(settings)) as client:
        def urlopen(request, timeout):
            assert not request.has_header("Authorization") and not request.has_header("Cookie")
            assert request.get_header("Host") == "api.example.com"
            methods.append(request.get_method())
            response = client.request(request.get_method(), request.full_url, headers=dict(request.header_items()))
            if response.status_code >= 400:
                raise urllib.error.HTTPError(request.full_url, response.status_code, "denied", response.headers, None)
            stream = io.BytesIO(response.content)
            stream.status = response.status_code
            stream.headers = response.headers
            return stream
        monkeypatch.setattr("scripts.anonymous_smoke.urllib.request.urlopen", urlopen)
        check_anonymous("http://testserver", "api.example.com")
    assert methods.count("OPTIONS") == 2 and methods.count("GET") == 6
