"""Independent durable reconciliation; discovery delivery never gates API activation."""

import asyncio
import json
import random
import re
from functools import partial

from .discovery_contract import https_url
from .discovery_outbox import DiscoveryOutbox
from .discovery_transport import DeliveryFailure, DiscoveryTransport
from .file_service import disk


class DiscoveryDelivery:
    def __init__(self, observer, transport=None):
        self.observer, self.runtime = observer, observer.runtime
        self.outbox = DiscoveryOutbox(observer.store)
        self.transport = transport or DiscoveryTransport()
        self.task = None
        self.dirty = True
        self.context = None
        self.state = "starting"
        self.revision = 0

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    def configuration(self):
        settings = self.runtime.settings
        if not all((settings.deployment_id, settings.public_url, settings.discovery_receiver_url,
                    settings.discovery_credential_secret)):
            raise ValueError("deployment configuration incomplete")
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,95}", settings.deployment_id):
            raise ValueError("deployment identity invalid")
        return (settings.deployment_id, https_url(settings.public_url, origin_only=True),
                https_url(settings.discovery_receiver_url))

    def desired(self, deployment, public_url, active, selected):
        intents = []
        with self.observer.store.connect() as db:
            observations = [json.loads(row[0]) for row in db.execute("SELECT body FROM observations_v1 LIMIT 1000")]
        for observation in observations:
            component = observation["component"]
            if selected is not None and component not in selected:
                continue
            previous = observation.get("lastValid")
            if not previous or not previous.get("source"):
                continue  # Pre-v2 observations require a fresh successful sidecar read.
            state, reason = observation["state"], observation.get("reason")
            current = active.get(component)
            withdrawal = "sidecar_removed" if state == "withdrawn" else {
                "source_removed": "component_removed", "source_disabled": "component_disabled"}.get(reason)
            if withdrawal:
                source = observation.get("source") or previous["source"]
                intents.append({"schema": "atrium.discovery-observation/v2", "deploymentId": deployment,
                    "component": component, "operation": "withdraw", "source": source,
                    "runtime": None, "sidecar": None, "reason": withdrawal})
            elif state == "valid" and current and current[0] == previous["generation"]:
                intents.append({"schema": "atrium.discovery-observation/v2", "deploymentId": deployment,
                    "component": component, "operation": "upsert", "source": previous["source"],
                    "runtime": {"apiBaseUrl": public_url + current[1]}, "sidecar": previous["sidecar"], "reason": None})
        return intents

    def allowed(self, envelope):
        component = envelope["component"]
        selected = self.runtime.settings.discovery_components
        if selected is not None and component not in selected:
            return False
        if envelope["operation"] == "withdraw":
            return True
        if component in self.runtime.availability.blocked:
            return False
        current = self.runtime.active.get(component)
        return bool(current and current.digest == envelope["source"]["specificationDigest"]
            and envelope["runtime"]["apiBaseUrl"] == self.runtime.settings.public_url.rstrip("/") + current.prefix)

    async def tick(self):
        try:
            deployment, public_url, receiver = self.configuration()
        except ValueError:
            self.state = "configuration_unavailable"
            return
        context = (deployment, public_url, receiver, tuple(sorted((key, value.digest, value.prefix)
            for key, value in self.runtime.active.items())), self.runtime.settings.discovery_components)
        if self.dirty or context != self.context:
            revision = self.revision
            active = {key: (value.digest, value.prefix) for key, value in self.runtime.active.items()}
            intents = await disk(self.desired, deployment, public_url, active, self.runtime.settings.discovery_components)
            for body in intents:
                await disk(self.outbox.ensure, body, receiver)
            self.context, self.dirty = context, self.revision != revision
        self.state = "active"
        for row in await disk(self.outbox.due, deployment, receiver):
            envelope = row["envelope"]
            if not self.allowed(envelope):
                continue
            try:
                try:
                    credential = await disk(self.runtime.settings.secret, self.runtime.settings.discovery_credential_secret)
                    if len(credential) > 8192 or "\n" in credential or "\r" in credential:
                        raise ValueError("invalid discovery credential")
                except (OSError, ValueError):
                    raise DeliveryFailure("credential_unavailable", 60) from None
                ack = await self.transport.send(receiver, credential, envelope)
                await disk(partial(self.outbox.finish, envelope, acknowledgement=ack))
            except DeliveryFailure as failure:
                delay = max(failure.delay, min(300, 5 * 2 ** min(row["attempts"], 6) * random.uniform(1, 1.2)))
                await disk(partial(self.outbox.finish, envelope, error=failure.reason, delay=delay))

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as error:
                self.state = "delivery_unavailable"
                self.runtime.record_failure(None, "discovery delivery", error)
            await asyncio.sleep(1)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.transport.close()
