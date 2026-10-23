"""Source authority, supersession and receiver contract failure boundaries."""

import copy
from pathlib import Path

import httpx
import pytest
import yaml

from looprd.discovery_contract import digest
from looprd.discovery_source import SourceSnapshot, observe
from looprd.discovery_transport import DeliveryFailure, DiscoveryTransport
from test_discovery_delivery import prepare, due, observe as reconcile
from test_discovery import sidecar
from discovery_receiver_fixture import ReceiverFixture


async def test_source_change_cas_and_blocking_never_delete_new_intent(setup_project, monkeypatch):
    settings, _, _ = setup_project
    path = Path(settings.sources).parent / "atriumd.yaml"; path.write_text(yaml.safe_dump(sidecar()))
    receiver = ReceiverFixture(); receiver.fail_status = 503
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    try:
        await reconcile(runtime); await delivery.tick()
        with delivery.observer.store.connect() as db:
            import json
            first = json.loads(db.execute("SELECT envelope FROM discovery_delivery_v2").fetchone()[0])
        changed = sidecar(); changed["game"]["title"] = "Changed"
        path.write_text(yaml.safe_dump(changed)); await reconcile(runtime); await delivery.tick()
        stale_ack = {"schema": "atrium.discovery-ack/v2", **{key: first[key] for key in
            ("deploymentId", "component", "operationId", "sequence", "fingerprint")}, "status": "pending", "change": "created"}
        delivery.outbox.finish(first, acknowledgement=stale_ack)
        assert delivery.outbox.summaries("node-test")[0]["state"] == "retrying"
        assert delivery.outbox.summaries("node-test")[0]["sequence"] == 2
        receiver.fail_status = None
        await runtime.set_blocked("demo", True); due(delivery); await delivery.tick()
        assert not receiver.posts
        await runtime.set_blocked("demo", False); await delivery.tick()
        assert receiver.posts[-1]["sidecar"]["game"]["title"] == "Changed"
    finally:
        await runtime.close()


async def test_configuration_failure_and_disabled_source_do_not_break_loading(setup_project, monkeypatch):
    settings, _, write_sources = setup_project
    (Path(settings.sources).parent / "atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
    receiver = ReceiverFixture()
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    try:
        settings.public_url = None
        await reconcile(runtime); await delivery.tick()
        assert "demo" in runtime.active and delivery.state == "configuration_unavailable"
        assert not receiver.posts
        settings.public_url = "https://api.example.com"
        await delivery.tick()
        write_sources([{"id": "demo", "specification": "looprd.yaml", "enabled": False}])
        await reconcile(runtime); await delivery.tick()
        assert receiver.posts[-1]["reason"] == "component_disabled"
        assert not runtime.active
    finally:
        await runtime.close()


async def test_authoritative_removal_withdraws_even_if_api_was_blocked(setup_project,monkeypatch):
    settings, _, write_sources=setup_project
    (Path(settings.sources).parent/"atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
    receiver=ReceiverFixture(); runtime,delivery=await prepare(settings,monkeypatch,receiver)
    try:
        await reconcile(runtime); await delivery.tick()
        await runtime.set_blocked("demo",True)
        write_sources([])
        await reconcile(runtime); await delivery.tick()
        assert receiver.posts[-1]["reason"]=="component_removed"
        assert delivery.outbox.summaries("node-test")[0]["state"]=="acknowledged"
    finally: await runtime.close()


async def test_receiver_rejects_wrong_scope_source_and_reordered_operations(setup_project, monkeypatch):
    settings, _, _ = setup_project
    (Path(settings.sources).parent / "atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
    receiver = ReceiverFixture()
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    try:
        await reconcile(runtime); await delivery.tick()
        first = receiver.posts[0]
        client = delivery.transport.client
        foreign = copy.deepcopy(first); foreign["deploymentId"] = "node-foreign"
        for name in ("fingerprint", "operationId", "sequence"): foreign.pop(name)
        foreign.update(operationId="a"*32, sequence=1, fingerprint=digest(foreign))
        response = await client.post(settings.discovery_receiver_url+"/observations",json=foreign,
            headers={"Authorization":"Bearer receiver-token"})
        assert response.status_code == 403
        receiver.allowed_sources = {"not-this-source"}
        response = await client.post(settings.discovery_receiver_url+"/observations",json=first,
            headers={"Authorization":"Bearer receiver-token"})
        assert response.status_code == 403
        receiver.allowed_sources = None
        newer = {**first,"sequence":2,"operationId":"b"*32}
        assert (await client.post(settings.discovery_receiver_url+"/observations",json=newer,
            headers={"Authorization":"Bearer receiver-token"})).status_code == 200
        older = {**first,"operationId":"c"*32}
        assert (await client.post(settings.discovery_receiver_url+"/observations",json=older,
            headers={"Authorization":"Bearer receiver-token"})).status_code == 409
    finally:
        await runtime.close()


@pytest.mark.parametrize("status,state", [(404,"unavailable"),(403,"unavailable"),(503,"unavailable"),(410,"absent")])
async def test_https_absence_requires_explicit_tombstone(setup_project,status,state):
    settings, _, _ = setup_project
    settings.discovery_https_origins=("https://metadata.example.com",)
    class Reader:
        async def read(self,location,**kwargs):
            response=httpx.Response(status,request=httpx.Request("GET",location)); response.raise_for_status()
    result=await observe(Reader(),SourceSnapshot("https://metadata.example.com/looprd.yaml","content","a"*64),"demo",settings)
    assert result["state"]==state


async def test_redirect_does_not_forward_credential_or_accept_huge_reply():
    calls=[]
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={"location":"https://elsewhere.example.com"})
    client=httpx.AsyncClient(transport=httpx.MockTransport(respond),follow_redirects=False)
    transport=DiscoveryTransport(client)
    try:
        with pytest.raises(DeliveryFailure): await transport.send("https://receiver.example.com","test-only",{})
        assert calls==["https://receiver.example.com/capabilities"]
    finally: await transport.close()
    transport=DiscoveryTransport(httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request:httpx.Response(200,content=b"x"*16385))))
    try:
        with pytest.raises(DeliveryFailure):
            await transport.send("https://receiver.example.com","test-only",{})
    finally: await transport.close()
