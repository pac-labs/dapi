"""Private, bounded control-plane observations; no application or catalog writes."""

import json
import os
import sqlite3
import time
from contextlib import contextmanager


class DiscoveryStore:
    def __init__(self, data):
        self.path = data / "discovery.sqlite3"
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS observations_v1 (component TEXT PRIMARY KEY, body TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, component, observation, snapshot=None):
        now = time.time()
        with self.connect() as db:
            row = db.execute("SELECT body FROM observations_v1 WHERE component=?", (component,)).fetchone()
            previous = json.loads(row[0]) if row else {}
            if not row and db.execute("SELECT COUNT(*) FROM observations_v1").fetchone()[0] >= 1000:
                raise ValueError("discovery observation limit reached")
            state = observation["state"]
            if state == "absent" and previous.get("lastValid"):
                state = "withdrawn"
            last_valid = previous.get("lastValid")
            if state == "valid":
                last_valid = {"sidecar": observation["sidecar"], "fingerprint": observation["fingerprint"],
                    "revision": snapshot.revision, "generation": snapshot.digest, "sourceIdentity": snapshot.identity}
            body = {"component": component, "mode": "observe-only", "delivery": "disabled",
                "state": state, "observedAt": now, "reason": observation.get("reason"),
                "stale": state not in ("valid", "absent"), "lastValid": last_valid,
                "revision": snapshot.revision if snapshot else previous.get("revision"),
                "generation": snapshot.digest if snapshot else previous.get("generation")}
            meaningful = lambda value: {key: item for key, item in value.items() if key not in ("observedAt", "changedAt")}
            body["changedAt"] = previous.get("changedAt", now) if meaningful(body) == meaningful(previous) else now
            db.execute("INSERT OR REPLACE INTO observations_v1 VALUES (?, ?)", (component, json.dumps(body)))
            return body

    def list(self):
        with self.connect() as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT body FROM observations_v1 ORDER BY component")]
        return [{**{key: value for key, value in row.items() if key != "lastValid"},
                 "fingerprint": row["lastValid"]["fingerprint"] if row.get("lastValid") else None} for row in rows]

    def get(self, component):
        with self.connect() as db:
            row = db.execute("SELECT body FROM observations_v1 WHERE component=?", (component,)).fetchone()
        return json.loads(row[0]) if row else None
