"""Fixed HTTPS receiver, rotated mounted credentials and bounded machine acknowledgements."""

import asyncio

import httpx

from .discovery_contract import Acknowledgement, Receiver


class DeliveryFailure(Exception):
    def __init__(self, reason, delay=0):
        self.reason, self.delay = reason, delay


class DiscoveryTransport:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False,
            trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))

    async def request(self, method, url, credential, body=None):
        try:
            async with self.client.stream(method, url, json=body,
                    headers={"Authorization": "Bearer " + credential, "Accept": "application/json"}) as response:
                if response.status_code in (401, 403):
                    raise DeliveryFailure("receiver_authentication", 60)
                if response.status_code not in (200, 201, 202):
                    retry = response.headers.get("retry-after", "")
                    delay = min(3600, int(retry)) if retry.isascii() and retry.isdecimal() and len(retry) <= 4 else 0
                    reason = "receiver_unavailable" if response.status_code in (408, 429) or response.status_code >= 500 else "receiver_contract"
                    raise DeliveryFailure(reason, max(delay, 60 if reason == "receiver_contract" else 0))
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 16384:
                        raise DeliveryFailure("receiver_response_invalid", 60)
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError:
            raise DeliveryFailure("receiver_unavailable") from None

    async def send(self, url, credential, envelope):
        try:
            async with asyncio.timeout(20):
                return await self.exchange(url, credential, envelope)
        except TimeoutError:
            raise DeliveryFailure("receiver_unavailable") from None

    async def exchange(self, url, credential, envelope):
        try:
            receiver = Receiver.model_validate_json(await self.request("GET", url + "/capabilities", credential))
            if (receiver.deploymentId != envelope["deploymentId"] or set(receiver.operations) != {"upsert", "withdraw"}
                    or envelope["source"]["kind"] not in receiver.provenanceKinds):
                raise DeliveryFailure("receiver_binding_mismatch", 60)
            ack = Acknowledgement.model_validate_json(await self.request("POST", url + "/observations", credential, envelope))
            for key in ("deploymentId", "component", "operationId", "sequence", "fingerprint"):
                if getattr(ack, key) != envelope[key]:
                    raise DeliveryFailure("receiver_ack_mismatch", 60)
            if (envelope["operation"] == "withdraw") != (ack.status == "withdrawn"):
                raise DeliveryFailure("receiver_ack_mismatch", 60)
            if (envelope["operation"] == "withdraw" and ack.change not in ("withdrawn", "unchanged")
                    or envelope["operation"] == "upsert" and ack.change == "withdrawn"):
                raise DeliveryFailure("receiver_ack_mismatch", 60)
            return ack.model_dump(by_alias=True)
        except ValueError:
            raise DeliveryFailure("receiver_response_invalid", 60) from None

    async def close(self):
        await self.client.aclose()
