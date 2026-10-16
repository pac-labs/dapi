"""Restricted ntfy topic transport; clients never choose an outbound host."""

import re
from urllib.parse import urlsplit

import httpx


def validate_provider(settings):
    for value, public in ((settings.push_public_url, True), (settings.push_delivery_url, False)):
        if value is None:
            continue
        url = urlsplit(value)
        if (url.scheme not in (("https",) if public else ("https", "http"))
                or not url.hostname or url.username or url.password or url.query or url.fragment
                or url.path not in ("", "/") or any(char.isspace() for char in value)):
            raise ValueError("push provider requires a plain origin URL")
        _ = url.port
    if not settings.push_public_url and (settings.push_delivery_url or settings.push_token_secret):
        raise ValueError("push delivery configuration requires a public provider URL")


def topic(settings, endpoint):
    if not settings.push_public_url:
        raise ValueError("push provider not configured")
    url, base = urlsplit(endpoint), urlsplit(settings.push_public_url)
    if (url.scheme != base.scheme or url.netloc != base.netloc or url.query or url.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_-]{1,200}", url.path)
            or endpoint != settings.push_public_url.rstrip("/") + url.path):
        raise ValueError("endpoint must be a topic on the configured push provider")
    return url.path


async def deliver(client, settings, endpoint, payload):
    path = topic(settings, endpoint)
    destination = (settings.push_delivery_url or settings.push_public_url).rstrip("/") + path
    headers = {"X-UnifiedPush": "1", "Content-Type": "application/octet-stream"}
    if settings.push_token_secret:
        headers["Authorization"] = "Bearer " + settings.secret(settings.push_token_secret)
    # Stream headers only: untrusted provider bodies cannot consume runtime memory.
    async with client.stream("POST", destination, headers=headers, content=payload) as response:
        return response.status_code


def client():
    return httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False)
