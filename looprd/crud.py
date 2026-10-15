"""Transactional native resource operations, policies, revisions and outbox writes."""

import json
import sqlalchemy as sa
from .outbox import EventError

class NativeCRUD:
    def _crud(self, name, operation, record_id, body, limit, offset, principal, params, revision):
        resource = self.spec.resources[name]
        table = self.storage.tables[resource.model]
        pk = next(iter(table.primary_key.columns))
        event = None
        with self.storage.sessions.begin() as session:
            if operation == "list":
                if resource.query:
                    return self.storage.queries.list(session, name, principal, params, limit, offset), 200, None
                rows = session.execute(sa.select(table).where(self.storage.policies.predicate(name, principal))
                                       .order_by(pk).limit(limit).offset(offset)).mappings()
                return [{field: row[field] for field in resource.projection} if resource.projection else dict(row) for row in rows], 200, None
            if self.storage.events and operation in ("create", "update", "delete"):
                self.storage.events.begin(session)
            if operation == "create":
                validator = self.storage.inputs[resource.model][0]
                if isinstance(body, dict) and resource.policy and resource.policy.ownerField:
                    body = self.storage.policies.create(session, name, principal, body)
                values = validator.model_validate_json(json.dumps(body)).model_dump()
                values = self.storage.policies.create(session, name, principal, values)
                if self.storage.quota:
                    self.storage.quota.lock(session)
                    self.storage.quota.check(session, table)
                if resource.revisionField:
                    values[resource.revisionField] = 1
                inserted = session.execute(table.insert().values(**values))
                record_id = inserted.inserted_primary_key[0]
                status, action = 201, "created"
            else:
                row = session.execute(sa.select(table).where(pk == record_id, self.storage.policies.predicate(name, principal))).mappings().first()
                if row is None:
                    return {"error": "record not found"}, 404, None
                if operation == "get":
                    return {field: row[field] for field in resource.projection} if resource.projection else dict(row), 200, None
                condition = pk == record_id
                if resource.revisionField:
                    if row[resource.revisionField] != revision:
                        raise EventError(412, "revision conflict; fetch the current record")
                    condition = sa.and_(condition, table.c[resource.revisionField] == revision)
                if operation == "delete":
                    changed = session.execute(table.delete().where(condition))
                    if changed.rowcount != 1:
                        raise EventError(412, "revision conflict")
                    status, action = 204, "deleted"
                else:
                    validator = self.storage.inputs[resource.model][1]
                    values = validator.model_validate_json(json.dumps(body)).model_dump(exclude_unset=True)
                    self.storage.policies.update(session, name, principal, dict(row), values)
                    if resource.revisionField:
                        values[resource.revisionField] = revision + 1
                    if values:
                        changed = session.execute(table.update().where(condition).values(**values))
                        if changed.rowcount != 1:
                            raise EventError(412, "revision conflict")
                    status, action = 200, "updated"
            result = None
            if action != "deleted":
                result = dict(session.execute(sa.select(table).where(pk == record_id)).mappings().one())
            event = json_safe({"component": self.spec.component, "resource": name,
                               "action": action, "id": record_id, "data": result})
            if self.storage.events and name in self.spec.websocket.resources:
                event = self.storage.events.emit(session, event, json_safe(dict(row) if action == "deleted" else result))
            if resource.policy:
                event["_looprd_record"] = json_safe(dict(row) if action == "deleted" else result)
        return result, status, event


def json_safe(value):
    return json.loads(json.dumps(value, default=lambda obj: obj.isoformat()))
