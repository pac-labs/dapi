"""One bounded persistent issue inbox shared by all components."""

import hashlib
import json
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager

from .bugreport_contract import normalize

LIMIT = 100


class BugReports:
    def __init__(self, data):
        self.path = data / "bugreports.sqlite3"
        self.lock = threading.Lock()
        self.attempts = deque(maxlen=120)
        try:
            with self.connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS issues_v1 (id TEXT PRIMARY KEY, component TEXT NOT NULL, first REAL NOT NULL, last REAL NOT NULL, occurrences INTEGER NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)")
            self.path.chmod(0o600)
        except (sqlite3.Error, OSError):
            pass  # Unavailable reporting must not prevent the APIs from starting.

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def permit(self, component):
        with self.lock:
            now = time.monotonic()
            while self.attempts and self.attempts[0][0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 120 or sum(c == component for _, c in self.attempts) >= 30:
                return False
            self.attempts.append((now, component))
            return True

    def submit(self, component, report):
        payload = report.sanitized()
        group = [component, payload["category"], payload["errorCode"],
                 normalize(payload["message"]), normalize(payload["stackTrace"])]
        identity = hashlib.sha256(json.dumps(group, ensure_ascii=True).encode()).hexdigest()
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT occurrences FROM issues_v1 WHERE id = ?", (identity,)).fetchone()
            if previous:
                count = min(previous["occurrences"] + 1, 9223372036854775807)
                db.execute("UPDATE issues_v1 SET last = ?, occurrences = ?, state = 'open', payload = ? WHERE id = ?",
                           (now, count, json.dumps(payload), identity))
            else:
                count = 1
                db.execute("INSERT INTO issues_v1 VALUES (?, ?, ?, ?, 1, 'open', ?)",
                           (identity, component, now, now, json.dumps(payload)))
                db.execute("DELETE FROM issues_v1 WHERE id IN (SELECT id FROM issues_v1 ORDER BY (state = 'resolved') DESC, last ASC, id ASC LIMIT MAX(0, (SELECT COUNT(*) FROM issues_v1) - ?))", (LIMIT,))
        return {"id": identity, "deduplicated": previous is not None, "occurrences": count}

    @staticmethod
    def describe(row, detail=False):
        payload = json.loads(row["payload"])
        result = {"id": row["id"], "component": row["component"], "firstSeenAt": row["first"] * 1000,
                  "lastSeenAt": row["last"] * 1000, "occurrences": row["occurrences"], "state": row["state"]}
        result.update(payload if detail else {key: payload[key] for key in ("appVersion", "category", "errorCode", "message", "platform")})
        return result

    def list(self):
        with self.connect() as db:
            return [self.describe(row) for row in db.execute("SELECT * FROM issues_v1 ORDER BY last DESC, id LIMIT ?", (LIMIT,))]

    def get(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT * FROM issues_v1 WHERE id = ?", (identity,)).fetchone()
        return self.describe(row, True) if row else None

    def update(self, identity, state):
        with self.connect() as db:
            return db.execute("UPDATE issues_v1 SET state = ? WHERE id = ?", (state, identity)).rowcount

    def delete(self, identity):
        with self.connect() as db:
            return db.execute("DELETE FROM issues_v1 WHERE id = ?", (identity,)).rowcount
