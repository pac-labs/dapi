"""Private file administration and unlisted capability upload/download routes."""

import json

from pydantic import Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import ClientDisconnect
from starlette.responses import FileResponse, JSONResponse, Response

from .file_store import FileError, LIFETIME
from .spec import Strict
from .web import admin_access

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff"}


class Invitation(Strict):
    filename: str = Field(min_length=1, max_length=180)

    @field_validator("filename")
    @classmethod
    def safe_name(cls, name):
        if name in (".", "..") or any(c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError("use a filename without directory paths or control characters")
        return name


def describe(row):
    result = {"id": row["id"], "filename": row["name"], "state": row["state"],
              "size": row["size"], "createdAt": row["created"] * 1000,
              "expiresAt": row["expires"] * 1000}
    if row["state"] == "ready":
        result["downloadUrl"] = "/_looprd/files/download/" + row["id"]
    return result


async def bounded_json(request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 2048:
            raise FileError(413, "invitation request exceeds limit")
        chunks.append(chunk)
    return Invitation.model_validate_json(b"".join(chunks))


async def manage(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    service = request.app.state.files
    if request.method == "GET":
        rows = await service.call("list")
        return JSONResponse({"files": [describe(row) for row in rows], "maxFileBytes": service.store.limit,
                             "quotaBytes": service.store.quota, "lifetimeSeconds": LIFETIME}, headers=HEADERS)
    if request.method == "DELETE":
        await service.call("remove", request.path_params["file_id"])
        return Response(status_code=204, headers=HEADERS)
    invitation = await bounded_json(request)
    row, token = await service.call("create", invitation.filename)
    result = describe(row)
    result["uploadUrl"] = "/_looprd/files/upload/" + token
    return JSONResponse(result, status_code=201, headers=HEADERS)


async def transfer(request):
    service = request.app.state.files
    if request.method == "PUT":
        # Do not claim a ticket for an accidental multipart invocation.
        if request.headers.get("content-type", "").lower().startswith("multipart/"):
            raise FileError(415, "send one raw file using curl --upload-file, not --form")
        row = await service.upload(request.path_params["token"], request)
        return JSONResponse(describe(row), status_code=201, headers=HEADERS)
    row = await service.call("get", request.path_params["file_id"])
    path = service.store.path(row["id"])
    if row["state"] != "ready" or not path.is_file():
        raise FileError(404, "file not found")
    return FileResponse(path, filename=row["name"], media_type="application/octet-stream",
                        headers={**HEADERS, "Content-Security-Policy": "sandbox; default-src 'none'"})


async def files_endpoint(request):
    try:
        if "token" in request.path_params or ("file_id" in request.path_params and request.method in ("GET", "HEAD")):
            return await transfer(request)
        return await manage(request)
    except FileError as error:
        return JSONResponse({"error": error.message}, status_code=error.status, headers=HEADERS)
    except (ValidationError, json.JSONDecodeError):
        return JSONResponse({"error": "provide one filename without a directory path (maximum 180 characters)"},
                            status_code=422, headers=HEADERS)
    except (TimeoutError, ClientDisconnect):
        return JSONResponse({"error": "upload interrupted; request a new invitation"}, status_code=408, headers=HEADERS)
    except (OSError, SQLAlchemyError):
        return JSONResponse({"error": "file storage unavailable"}, status_code=503, headers=HEADERS)
