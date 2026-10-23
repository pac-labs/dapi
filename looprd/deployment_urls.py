"""Validation for operator-configured deployment URLs, independent of consumers."""

from urllib.parse import urlsplit


def https_url(value, *, origin_only=False):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.port not in (None, 443) or url.query or url.fragment
            or origin_only and url.path not in ("", "/")):
        raise ValueError("host-configured HTTPS URL required")
    return value.rstrip("/")
