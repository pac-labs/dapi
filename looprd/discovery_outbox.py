"""One durable desired intent per deployment/component in the observation database."""

import json
import secrets
import time

from .discovery_contract import Observation, digest


class DiscoveryOutbox:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS discovery_delivery_v2 (
                deployment TEXT NOT NULL, component TEXT NOT NULL, sequence INTEGER NOT NULL,
                desired TEXT NOT NULL, binding TEXT NOT NULL, envelope TEXT NOT NULL,
                state TEXT NOT NULL, attempts INTEGER NOT NULL, due REAL NOT NULL,
                error TEXT, acknowledged TEXT, PRIMARY KEY(deployment, component))""")

    def ensure(self, body, receiver):
        desired = digest({"body": body, "receiver": receiver})
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT desired, sequence FROM discovery_delivery_v2 WHERE deployment=? AND component=?",
                (body["deploymentId"], body["component"])).fetchone()
            if old and old[0] == desired:
                return
            if not old and db.execute("SELECT COUNT(*) FROM discovery_delivery_v2").fetchone()[0] >= 1000:
                raise ValueError("discovery delivery limit reached")
            sequence = old[1] + 1 if old else 1
            envelope = {**body, "sequence": sequence, "operationId": secrets.token_hex(16), "fingerprint": digest(body)}
            Observation.model_validate(envelope)
            db.execute("""INSERT INTO discovery_delivery_v2 VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, NULL, NULL)
                ON CONFLICT(deployment,component) DO UPDATE SET sequence=excluded.sequence, desired=excluded.desired,
                binding=excluded.binding, envelope=excluded.envelope, state='pending', attempts=0,
                due=excluded.due, error=NULL""",
                (body["deploymentId"], body["component"], sequence, desired, digest(receiver),
                 json.dumps(envelope), time.time()))

    def due(self, deployment, receiver):
        with self.store.connect() as db:
            rows = db.execute("""SELECT component,envelope,attempts FROM discovery_delivery_v2
                WHERE deployment=? AND binding=? AND state<>'acknowledged' AND due<=?
                ORDER BY due,component LIMIT 16""", (deployment, digest(receiver), time.time())).fetchall()
        return [{"component": row[0], "envelope": json.loads(row[1]), "attempts": row[2]} for row in rows]

    def finish(self, envelope, *, acknowledgement=None, error=None, delay=0):
        """CAS: an old response cannot acknowledge a superseding intent."""
        with self.store.connect() as db:
            db.execute("""UPDATE discovery_delivery_v2 SET state=?, attempts=MIN(attempts+1,1000000000),
                due=?, error=?, acknowledged=COALESCE(?,acknowledged)
                WHERE deployment=? AND component=? AND sequence=? AND json_extract(envelope,'$.operationId')=?""",
                ("acknowledged" if acknowledgement else "retrying", time.time() + delay, error,
                 json.dumps(acknowledgement) if acknowledgement else None, envelope["deploymentId"],
                 envelope["component"], envelope["sequence"], envelope["operationId"]))

    def summaries(self, deployment):
        with self.store.connect() as db:
            rows = db.execute("""SELECT component,sequence,state,attempts,due,error,acknowledged
                FROM discovery_delivery_v2 WHERE deployment=? ORDER BY component LIMIT 1000""", (deployment,)).fetchall()
        return [{"component": row[0], "sequence": row[1], "state": row[2], "attempts": row[3],
            "nextAttemptAt": row[4] if row[2] != "acknowledged" else None, "error": row[5],
            "acknowledgedSequence": json.loads(row[6])["sequence"] if row[6] else None} for row in rows]
