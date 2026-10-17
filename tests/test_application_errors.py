"""Generic application rejection transport never reflects details or headers."""

import pytest
from starlette.testclient import TestClient

from looprd.app import create_app
from test_http import BASE, HEADERS


@pytest.mark.parametrize("status", [400, 403, 409, 422, 429, 503, 307])
def test_intentional_http_rejections_are_safe(setup_project, spec_data, status):
    settings, write, _ = setup_project
    settings.allow_python = True
    spec_data["handlers"] = [{"path": "/reject", "kind": "python", "source":
        "from starlette.exceptions import HTTPException\nasync def handle(ctx):\n"
        f"    raise HTTPException(status_code={status}, detail='private-token-and-sql', headers={{'Retry-After':'12','Location':'https://secret.example','Set-Cookie':'secret'}})\n"}]
    write(spec_data)
    with TestClient(create_app(settings)) as client:
        result = client.get(BASE + "/reject", headers=HEADERS, follow_redirects=False)
        assert result.status_code == (status if status >= 400 else 500)
        assert result.headers.get("Retry-After") == ("12" if status in (429, 503) else None)
        assert "location" not in result.headers and "set-cookie" not in result.headers
        assert "private-token" not in result.text and "sql" not in result.text
        assert result.headers["Cache-Control"] == "no-store"
