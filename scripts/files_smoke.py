"""Disposable single-file round trip, including a public reverse-proxy path."""

import json
import urllib.error
import urllib.request


def smoke_files(management_base, transfer_base=None, host=None, admin="admin"):
    transfer_base = transfer_base or management_base

    def request(base, path, method="GET", data=None, private=False):
        headers = {"Host": host} if host else {}
        if private:
            headers["Authorization"] = "Bearer " + admin
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    status, raw = request(management_base, "/_looprd/files", "POST",
                          json.dumps({"filename": "looprd-smoke.txt"}).encode(), True)
    assert status == 201
    ticket = json.loads(raw)
    try:
        status, raw = request(transfer_base, ticket["uploadUrl"], "PUT", b"looprd-file-smoke")
        assert status == 201, (status, raw)
        download = json.loads(raw)["downloadUrl"]
        assert request(transfer_base, download) == (200, b"looprd-file-smoke")
        assert request(transfer_base, ticket["uploadUrl"], "PUT", b"again")[0] == 409
        assert request(transfer_base, "/_looprd/files")[0] in (401, 404)
        assert request(management_base, "/_looprd/files/" + ticket["id"], "DELETE", private=True)[0] == 204
        assert request(transfer_base, download)[0] == 404
    finally:
        request(management_base, "/_looprd/files/" + ticket["id"], "DELETE", private=True)
    print("File smoke passed: invitation, single-use upload, anonymous download, private listing, deletion")
