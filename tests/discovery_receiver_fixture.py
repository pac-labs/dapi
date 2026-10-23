"""Contract-only Atrium v2 fixture. This is not installed Atrium receiver code."""

import json

import httpx

from looprd.discovery_contract import Observation


class ReceiverFixture:
    def __init__(self):
        self.credentials = {"receiver-token": "node-test"}
        self.operations = {}
        self.current = {}
        self.requests = []
        self.posts = []
        self.fail_status = None
        self.lose_ack = False
        self.reviewed = False
        self.bad_ack = False
        self.wrong_binding = False
        self.legacy = False
        self.allowed_sources = None

    def __call__(self, request):
        self.requests.append(request)
        principal = self.credentials.get(request.headers.get("authorization", "").removeprefix("Bearer "))
        if principal is None:
            return httpx.Response(401)
        if request.method == "GET":
            if self.legacy:
                return httpx.Response(200, json={"schema": "atrium.discovery-queue/v1", "candidates": []})
            return httpx.Response(200, json={"schema": "atrium.discovery-capabilities/v2",
                "deploymentId": "another-node" if self.wrong_binding else principal,
                "operations": ["upsert", "withdraw"], "provenanceKinds": ["git", "local", "https"]})
        if self.fail_status:
            return httpx.Response(self.fail_status, headers={"Retry-After": "30"})
        try:
            body = Observation.model_validate_json(request.content).model_dump(by_alias=True)
        except ValueError:
            return httpx.Response(422)
        self.posts.append(body)
        # The actual receiver must bind principal to deployment, component AND approved source.
        if body["deploymentId"] != principal or body["component"] != "demo":
            return httpx.Response(403)
        if self.allowed_sources is not None and body["source"]["identity"] not in self.allowed_sources:
            return httpx.Response(403)
        operation = body["operationId"]
        previous = self.operations.get(operation)
        key = (principal, body["component"])
        if previous and previous["body"] != body:
            return httpx.Response(409)
        if not previous and key in self.current and self.current[key]["sequence"] >= body["sequence"]:
            return httpx.Response(409)
        if not previous:
            self.current[key] = body
        ack = {"schema": "atrium.discovery-ack/v2", **{name: body[name] for name in
            ("deploymentId", "component", "operationId", "sequence", "fingerprint")},
            "status": "withdrawn" if body["operation"] == "withdraw" else "reviewed" if self.reviewed else "pending",
            "change": "unchanged" if previous else "withdrawn" if body["operation"] == "withdraw" else "created"}
        self.operations[operation] = {"body": body, "ack": ack}
        if self.lose_ack:
            self.lose_ack = False
            raise httpx.ReadTimeout("synthetic lost acknowledgement", request=request)
        if self.bad_ack:
            ack = {**ack, "fingerprint": "0" * 64}
        return httpx.Response(200, json=ack)
