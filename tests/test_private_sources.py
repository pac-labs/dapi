"""Authenticated GitHub sources, pinned relative specs, and credential containment."""

import httpx
import pytest

from looprd.sources import SourceReader
from looprd.spec import Source


async def reader_with_transport(location, handler):
    reader = SourceReader(location)
    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return reader


async def test_private_index_and_referenced_repository_use_token_file(tmp_path, monkeypatch):
    token_file = tmp_path / "github-token"
    token_file.write_text("first-token\n", encoding="utf-8")
    monkeypatch.setenv("LOOPRD_GITHUB_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("LOOPRD_GITHUB_TOKEN", "ignored-environment-token")
    calls = []
    index_sha, component_sha = "a" * 40, "b" * 40

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer first-token"
        if "/commits/" in request.url.path:
            sha = index_sha if "/config/" in request.url.path else component_sha
            return httpx.Response(200, json={"sha": sha})
        assert request.headers["accept"] == "application/vnd.github.raw+json"
        if request.url.path.endswith("sources.yaml"):
            assert request.url.params["ref"] == index_sha
            return httpx.Response(200, text=(
                "apiVersion: looprd/v1\ncomponents:\n"
                "  - id: demo\n    repository: https://github.com/pac-labs/private-app.git\n"
            ))
        assert request.url.params["ref"] == component_sha
        return httpx.Response(200, text=(
            "apiVersion: looprd/v1\ncomponent: demo\nrelease: '1'\n"
            "api: {version: v1}\nauth: {mode: public}\n"
        ))

    reader = await reader_with_transport("github://pac-labs/config/looprd/sources.yaml?ref=main", respond)
    try:
        sources = await reader.sources()
        spec, revision = await reader.component(sources.components[0])
        assert spec.component == "demo" and revision == component_sha
        assert len(calls) == 4
        assert all(request.url.host == "api.github.com" for request in calls)
    finally:
        await reader.close()


async def test_relative_spec_uses_same_index_commit():
    sha = "c" * 40
    calls = []

    def respond(request):
        calls.append(request)
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": sha})
        assert request.url.params["ref"] == sha
        if request.url.path.endswith("sources.yaml"):
            return httpx.Response(200, text="apiVersion: looprd/v1\ncomponents: [{id: demo, specification: demo.yaml}]")
        assert request.url.path == "/repos/pac-labs/config/contents/looprd/demo.yaml"
        return httpx.Response(200, text="apiVersion: looprd/v1\ncomponent: demo\nrelease: '1'\napi: {version: v1}\nauth: {mode: public}")

    reader = await reader_with_transport("github://pac-labs/config/looprd/sources.yaml?ref=main", respond)
    try:
        sources = await reader.sources()
        await reader.component(sources.components[0])
        assert len(calls) == 3, "pinned relative spec must not re-resolve the moving branch"
    finally:
        await reader.close()


async def test_github_token_not_forwarded_to_other_hosts_or_redirects(monkeypatch):
    monkeypatch.delenv("LOOPRD_GITHUB_TOKEN_FILE", raising=False)
    monkeypatch.setenv("LOOPRD_GITHUB_TOKEN", "private-token")
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "api.github.com":
            assert request.headers["authorization"] == "Bearer private-token"
            return httpx.Response(302, headers={"Location": "https://other.example/redirect"})
        assert "authorization" not in request.headers
        return httpx.Response(200, text="document")

    reader = await reader_with_transport("https://other.example/sources.yaml", respond)
    try:
        await reader.read("https://other.example/sources.yaml")
        with pytest.raises(httpx.HTTPStatusError):
            await reader.read("https://api.github.com/repos/pac-labs/config/contents/sources.yaml")
        assert len(calls) == 2
    finally:
        await reader.close()


async def test_token_file_rotation_and_missing_file_fail_closed(tmp_path, monkeypatch):
    token_file = tmp_path / "github-token"
    monkeypatch.setenv("LOOPRD_GITHUB_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("LOOPRD_GITHUB_TOKEN", "must-not-fallback")
    tokens = []

    def respond(request):
        tokens.append(request.headers["authorization"])
        return httpx.Response(200, text="ok")

    reader = await reader_with_transport("https://api.github.com/", respond)
    try:
        token_file.write_text("one", encoding="utf-8")
        await reader.read("https://api.github.com/repos/pac-labs/config/contents/sources.yaml")
        token_file.write_text("two", encoding="utf-8")
        await reader.read("https://api.github.com/repos/pac-labs/config/contents/sources.yaml")
        token_file.unlink()
        with pytest.raises(FileNotFoundError):
            await reader.read("https://api.github.com/repos/pac-labs/config/contents/sources.yaml")
        assert tokens == ["Bearer one", "Bearer two"]
    finally:
        await reader.close()
