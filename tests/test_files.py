"""Operator invitations, single-file transfers, and bounded retention."""

import asyncio
import time

import pytest
from sqlalchemy import update
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.file_service import FileService
from looprd.file_store import FileError, FileStore, LIFETIME
from test_http import ADMIN


def invite(client, name="report.txt"):
    response = client.post("/_looprd/files", json={"filename": name}, headers=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def test_private_invitation_unlisted_public_download_and_deletion(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.get("/_looprd/files").status_code == 401
        assert client.post("/_looprd/files", json={"filename": "x"}).status_code == 401
        ticket = invite(client, "résumé.html")
        assert "downloadUrl" not in ticket
        assert ticket["expiresAt"] - ticket["createdAt"] == pytest.approx(LIFETIME * 1000)
        file_id, url = ticket["id"], ticket["uploadUrl"]
        assert client.get("/_looprd/files/download/" + file_id).status_code == 404
        assert client.get(url).status_code == 405
        response = client.put(url, content=b"<script>file content</script>")
        assert response.status_code == 201
        download = response.json()["downloadUrl"]
        response = client.get(download)
        assert response.content == b"<script>file content</script>"
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert client.head(download).headers["content-length"] == str(len(response.content))
        assert client.get(download, headers={"Range": "bytes=0-2"}).content == b"<sc"
        assert client.put(url, content=b"replacement").status_code == 409
        listing = client.get("/_looprd/files", headers=ADMIN).json()
        assert listing["files"][0]["downloadUrl"] == download
        assert "uploadUrl" not in listing["files"][0] and "upload_hash" not in str(listing)
        assert client.get("/_looprd/files/download/").status_code in (404, 405)
        assert client.delete("/_looprd/files/" + file_id).status_code == 401
        assert client.delete("/_looprd/files/" + file_id, headers=ADMIN).status_code == 204
        assert client.get(download).status_code == 404
        assert client.put(url, content=b"again").status_code == 404


def test_restart_retains_file_and_single_use_without_plaintext_upload_token(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        ticket = invite(client)
        response = client.put(ticket["uploadUrl"], content=b"retained")
        download = response.json()["downloadUrl"]
    assert ticket["uploadUrl"].split("/")[-1].encode() not in (settings.data / "files/index.sqlite").read_bytes()
    with TestClient(create_app(settings)) as client:
        assert client.get(download).content == b"retained"
        assert client.put(ticket["uploadUrl"], content=b"again").status_code == 409


def test_expiry_rejects_download_and_upload_and_removes_bytes(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        ready, pending = invite(client), invite(client)
        download = client.put(ready["uploadUrl"], content=b"expires").json()["downloadUrl"]
        store = client.app.state.files.store
        with store.engine.begin() as db:
            db.execute(update(store.files).values(expires=time.time() - 1))
        assert client.get(download).status_code == 404
        assert client.put(pending["uploadUrl"], content=b"late").status_code == 404
        assert client.get("/_looprd/files", headers=ADMIN).json()["files"] == []
        assert not list(store.root.glob("*.blob"))


def test_limits_failed_upload_cleanup_and_quota(setup_project):
    settings, _, _ = setup_project
    settings.file_max_bytes, settings.file_quota_bytes = 8, 8
    with TestClient(create_app(settings)) as client:
        failed = invite(client)
        assert client.put(failed["uploadUrl"], content=b"too large").status_code == 413
        assert client.put(failed["uploadUrl"], content=b"retry").status_code == 409
        assert not list((settings.data / "files").glob("*.blob"))
        first, second = invite(client), invite(client)
        assert client.put(first["uploadUrl"], content=b"12345678").status_code == 201
        assert client.put(second["uploadUrl"], content=b"x").status_code == 507
        assert client.delete("/_looprd/files/" + first["id"], headers=ADMIN).status_code == 204
        assert client.put(second["uploadUrl"], content=b"x").status_code == 201


@pytest.mark.parametrize("filename", ["../secret", "a/b", "a\\b", "\nheader", "\x7f", "", ".", "x" * 181])
def test_filename_boundary(setup_project, filename):
    with TestClient(create_app(setup_project[0])) as client:
        assert client.post("/_looprd/files", json={"filename": filename}, headers=ADMIN).status_code == 422


def test_bounded_request_multipart_and_unknown_links(setup_project):
    with TestClient(create_app(setup_project[0])) as client:
        assert client.post("/_looprd/files", content=b"x" * 2049, headers=ADMIN).status_code == 413
        assert client.post("/_looprd/files", json={"filename": "x", "extra": True}, headers=ADMIN).status_code == 422
        ticket = invite(client)
        assert client.put(ticket["uploadUrl"], files={"file": ("a", b"b")}).status_code == 415
        assert client.put(ticket["uploadUrl"], content=b"").status_code == 201
        assert client.put("/_looprd/files/upload/guess", content=b"x").status_code == 404
        assert client.get("/_looprd/files/download/guess").status_code == 404


@pytest.mark.asyncio
async def test_concurrent_claim_cancellation_and_crash_recovery(setup_project):
    store = FileStore(setup_project[0])
    service = FileService(store)
    row, token = await service.call("create", "x")
    started = asyncio.Event()
    class Interrupted:
        async def stream(self):
            yield b"partial"
            started.set()
            await asyncio.sleep(60)
    task = asyncio.create_task(service.upload(token, Interrupted()))
    await asyncio.wait_for(started.wait(), 5)
    with pytest.raises(FileError) as error:
        await service.call("claim", token)
    assert error.value.status == 409
    with pytest.raises(FileError):
        await service.call("remove", row["id"])
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await service.call("get", row["id"]))["state"] == "failed"
    assert not store.path(row["id"]).exists()
    crash, token = await service.call("create", "crash")
    await service.call("claim", token)
    store.path(crash["id"]).write_bytes(b"uncommitted")
    await service.close()
    reopened = FileStore(setup_project[0])
    assert reopened.get(crash["id"])["state"] == "failed"
    assert not reopened.path(crash["id"]).exists()
    reopened.close()


def test_pending_limit_and_panel_boundary(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        for _ in range(128):
            invite(client)
        assert client.post("/_looprd/files", json={"filename": "x"}, headers=ADMIN).status_code == 409
        page = client.get("/_looprd/panel/").text
        assert 'data-view="files"' in page and 'Request upload URL' in page and 'Copy download URL' in page
        assert 'curl --fail-with-body --upload-file' in page
