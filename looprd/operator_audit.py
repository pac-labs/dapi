"""Transactional receipts and append-only metadata for explicit operator writes."""

import time
import sqlalchemy as sa

from .bugreport_contract import redact


class OperatorAudit:
    def __init__(self):
        self.table = sa.Table("_looprd_operator_audit_v1", sa.MetaData(),
            sa.Column("id", sa.String(64), primary_key=True), sa.Column("at", sa.Float, nullable=False),
            sa.Column("operation", sa.String(100), nullable=False), sa.Column("target", sa.String(100), nullable=False),
            sa.Column("reason", sa.String(1000), nullable=False), sa.Column("generation", sa.String(64), nullable=False),
            sa.Column("requestHash", sa.String(64), nullable=False), sa.Column("changes", sa.JSON, nullable=False))

    def initialize(self, connection):
        self.table.create(connection, checkfirst=True)

    def existing(self, session, identity):
        return session.execute(sa.select(self.table).where(self.table.c.id == identity)).mappings().first()

    def record(self, session, generation, payload, request_hash, operation, target, changes):
        session.execute(self.table.insert().values(id=payload.operationId, at=time.time(), operation=operation,
            target=target, reason=redact(payload.reason)[:1000], generation=generation.digest,
            requestHash=request_hash, changes=changes))

    def list(self, session, offset):
        rows = session.execute(sa.select(self.table).order_by(self.table.c.at.desc(), self.table.c.id)
                               .limit(51).offset(offset)).mappings().all()
        return {"entries": [{"id": row["id"], "at": row["at"] * 1000, "operation": row["operation"],
                             "target": row["target"], "reason": row["reason"], "changes": row["changes"],
                             "actor": "management-key", "generation": row["generation"]} for row in rows[:50]],
                "nextOffset": offset + 50 if len(rows) > 50 else None}
