"""Revision lookup must not download a commit's potentially enormous file diff."""

import httpx
import pytest

from looprd.sources import SourceReader, MAX_DOCUMENT
from looprd.spec import Source


async def test_large_commit_metadata_does_not_prevent_pinned_spec_fetch():
    sha = "a" * 40
    calls = []

    def respond(request):
        calls.append(request)
        if "/commits/" in request.url.path:
            if request.headers.get("accept") == "application/vnd.github.sha":
                return httpx.Response(200, text=sha + "\n")
            return httpx.Response(200, json={"sha": sha, "files": [{"patch": "x" * (MAX_DOCUMENT + 1)}]})
        assert request.headers["accept"] == "application/vnd.github.raw+json"
        assert request.url.params["ref"] == sha
        return httpx.Response(200, text="apiVersion: looprd/v1\ncomponent: demo\nrelease: '2'\napi: {version: v1}\nauth: {mode: public}")

    reader = SourceReader("unused")
    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        specification, revision = await reader.component(Source(id="demo", repository="https://github.com/example/demo", ref="feature/test"))
        assert revision == sha and specification.release == "2"
        assert len(calls) == 2
        assert calls[0].url.raw_path.endswith(b"feature%2Ftest")
    finally:
        await reader.close()


@pytest.mark.parametrize("body", ["", "not-a-commit", '{"sha":"' + "a" * 40 + '"}', "g" * 40, "a" * 41])
async def test_revision_lookup_rejects_invalid_sha_responses(body):
    reader = SourceReader("unused")
    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    try:
        with pytest.raises(ValueError, match="invalid resolved GitHub revision"):
            await reader.resolve_revision("example/demo", "main")
    finally:
        await reader.close()


async def test_immutable_revision_needs_no_network_lookup():
    reader = SourceReader("unused")
    await reader.client.aclose()
    try:
        assert await reader.resolve_revision("example/demo", "b" * 40) == "b" * 40
    finally:
        await reader.close()
