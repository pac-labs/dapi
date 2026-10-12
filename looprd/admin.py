"""Persistent management-key bootstrap; only hashes live in the data volume."""

import hashlib
import hmac
import json
import os
import secrets
import tempfile


def mounted(settings):
    return (settings.secrets / settings.admin_secret).exists()


def read_digest(settings):
    record = json.loads((settings.data / "admin-key.json").read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("invalid management key record")
    digest = record.get("sha256", "")
    if record.get("version") != 1 or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid management key record")
    return digest


def provision(settings, rotate=False):
    """Return a new secret once; preserve existing credentials unless explicitly rotated."""
    if mounted(settings):
        settings.secret(settings.admin_secret)  # Fail closed for an empty/unreadable mounted credential.
        if rotate:
            raise ValueError("rotate the mounted admin-token file on the host")
        return None
    target = settings.data / "admin-key.json"
    if target.exists() and not rotate:
        read_digest(settings)
        return None
    settings.data.mkdir(parents=True, exist_ok=True)
    token = "lra_" + secrets.token_urlsafe(32)
    record = {"version": 1, "sha256": hashlib.sha256(token.encode()).hexdigest()}
    descriptor, temporary = tempfile.mkstemp(prefix=".admin-key-", dir=settings.data)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if rotate:
            os.replace(temporary, target)
        else:
            # Exclusive, atomic publication prevents competing startups replacing a key.
            try:
                os.link(temporary, target)
            except FileExistsError:
                read_digest(settings)
                return None
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return token


def authorized(settings, authorization):
    if mounted(settings):
        return hmac.compare_digest((authorization or "").encode(), f"Bearer {settings.secret(settings.admin_secret)}".encode())
    digest = read_digest(settings)
    if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 256:
        return False
    return hmac.compare_digest(hashlib.sha256(authorization[7:].encode()).hexdigest(), digest)


def announce(token):
    if token:
        print("Looprd management API key (shown once; save it securely):", flush=True)
        print(token, flush=True)
        print("Open /_looprd/panel/ and paste this key. Recover by running: python -m looprd admin-key --rotate", flush=True)
