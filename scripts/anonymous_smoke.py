"""Credential-free JSON reads through the shipped proxy, with private-route denial."""

import json
import urllib.error
import urllib.request


def check_anonymous(base, host):
    for prefix in ("/looprd", "/dapi"):
        path = prefix + "/public-read/v1/catalog"
        for method in ("GET", "OPTIONS"):
            request = urllib.request.Request(base + path, method=method, headers={
                "Host": host, "Origin": "https://unlisted.example.com",
                **({"Access-Control-Request-Method": "GET"} if method == "OPTIONS" else {}),
            })
            with urllib.request.urlopen(request, timeout=3) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store, max-age=0"
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Access-Control-Allow-Origin"] == "*"
                assert response.headers["Content-Type"] == "application/json"
                assert response.headers.get("Access-Control-Allow-Credentials") is None
                data = json.load(response)
                assert data == ({"items": []} if method == "GET" else {"methods": ["GET"]})
        for suffix in ("/private", "/openapi.json"):
            request = urllib.request.Request(base + prefix + "/public-read/v1" + suffix, headers={"Host": host})
            try:
                urllib.request.urlopen(request, timeout=3)
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("proxy exposed a private component route")
