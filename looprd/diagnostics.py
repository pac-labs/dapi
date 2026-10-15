"""Bounded persistent failure history and revocable, read-only capabilities."""

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager

from .failures import explain


class Diagnostics:
    def __init__(self, data):
        self.path = data / "diagnostics.sqlite3"
        try:
            with self.connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS failures_v1 (id TEXT PRIMARY KEY, component TEXT, stage TEXT, at REAL, body TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS links_v1 (id TEXT PRIMARY KEY, digest TEXT UNIQUE, component TEXT, expires REAL, revoked INTEGER DEFAULT 0)")
        except sqlite3.Error:
            # Preserve the file for operator repair; reads/minting fail closed with 503.
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, component, stage, error):
        body = explain(error)
        identity = hashlib.sha256(json.dumps([component, stage, body], sort_keys=True).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO failures_v1 VALUES (?, ?, ?, ?, ?)",
                       (identity, component, stage, time.time(), json.dumps(body)))
            db.execute("DELETE FROM failures_v1 WHERE at < ? OR id IN (SELECT id FROM failures_v1 ORDER BY at DESC LIMIT -1 OFFSET 500)", (time.time() - 604800,))

    def failures(self, component=None):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM failures_v1 WHERE (? IS NULL OR component = ?) AND at >= ? ORDER BY at DESC LIMIT 100",
                              (component, component, time.time() - 604800)).fetchall()
        return [{"id": row["id"], "component": row["component"], "stage": row["stage"],
                 "lastSeenAt": row["at"], **json.loads(row["body"])} for row in rows]

    def create(self, component, seconds):
        token, identity = secrets.token_urlsafe(32), secrets.token_hex(12)
        expires = time.time() + seconds
        with self.connect() as db:
            db.execute("DELETE FROM links_v1 WHERE expires <= ? OR revoked = 1", (time.time(),))
            if db.execute("SELECT COUNT(*) FROM links_v1").fetchone()[0] >= 100:
                raise ValueError("diagnostic link limit reached")
            db.execute("INSERT INTO links_v1 (id, digest, component, expires) VALUES (?, ?, ?, ?)",
                       (identity, hashlib.sha256(token.encode()).hexdigest(), component, expires))
        return {"id": identity, "component": component, "expiresAt": expires,
                "path": "/_looprd/diagnostic/" + token}

    def authorize(self, token):
        if len(token) != 43:
            return None
        with self.connect() as db:
            row = db.execute("SELECT id, component, expires FROM links_v1 WHERE digest = ? AND revoked = 0 AND expires > ?",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return dict(row) if row else None

    def links(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id, component, expires AS expiresAt FROM links_v1 WHERE revoked = 0 AND expires > ? ORDER BY expires DESC", (time.time(),))]

    def revoke(self, identity):
        with self.connect() as db:
            return db.execute("UPDATE links_v1 SET revoked = 1 WHERE id = ?", (identity,)).rowcount


def snapshot(runtime, component=None):
    known = set(runtime.active) | set(runtime.sources) | set(runtime.errors)
    components = []
    for identity in sorted(known):
        if component is not None and component != identity:
            continue
        generation = runtime.active.get(identity)
        item = {"id": identity, "loaded": generation is not None,
                "blocked": identity in runtime.availability.blocked,
                "currentFailure": runtime.errors.get(identity),
                "lastWorkingRetained": generation is not None and runtime.errors.get(identity, "").startswith(("load:", "restore:", "dependency:"))}
        if generation:
            item.update(release=generation.spec.release, schemaVersion=generation.spec.database.schemaVersion,
                        backend=generation.spec.database.backend, apiVersion=generation.spec.api.version,
                        jobFailure=generation.job_error, eventRelayFailure=generation.relay_error)
        components.append(item)
    return {"apiVersion": "looprd-diagnostics/v1", "kind": "DiagnosticSnapshot", "generatedAt": time.time(),
            "contractPath": "/_looprd/diagnostics.openapi.json",
            "scope": {"component": component, "readOnly": True, "applicationData": False},
            "sourcesFailed": bool(runtime.sources_error) if component is None else None,
            "components": components, "failures": runtime.diagnostics.failures(component),
            "historyRetentionSeconds": 604800}
