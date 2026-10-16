"""Projected reads, constrained edits and transaction-bound trusted operator actions."""

import hashlib
import hmac
import inspect
import json
import secrets
import time

import sqlalchemy as sa
from pydantic import ConfigDict, Field, create_model

from .auth import Principal
from .crud import json_safe


class AdministrationError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def etag(row, key):
    return hmac.new(key, json.dumps(json_safe(dict(row)), sort_keys=True, allow_nan=False).encode(), hashlib.sha256).hexdigest()


def constraints(value, policy):
    def same(left, right):
        return (type(left) is type(right) or type(left) in (int, float) and type(right) in (int, float)) and left == right
    if policy.choices is not None and not any(same(value, choice) for choice in policy.choices):
        raise AdministrationError(422, "value_not_in_choices")
    if value is None:
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if policy.minimum is not None and value < policy.minimum or policy.maximum is not None and value > policy.maximum:
            raise AdministrationError(422, "value_outside_bounds")


class Administration:
    def __init__(self, generation):
        self.generation, self.spec, self.storage = generation, generation.spec, generation.storage
        self._etag_key = secrets.token_bytes(32)
        self.actions, self.inputs = {}, {}
        for name, action in self.spec.management.actions.items():
            namespace = {"__name__": "looprd_operator_" + name}
            exec(compile(action.source, "<looprd-management-action>", "exec"), namespace)
            function = namespace.get("handle")
            if not inspect.isfunction(function) or inspect.iscoroutinefunction(function) or inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
                raise ValueError("management action must define synchronous def handle(ctx)")
            try:
                inspect.signature(function).bind(None)
            except TypeError as error:
                raise ValueError("management action must accept one context argument") from error
            self.actions[name] = function
            fields = {}
            for field, parameter in action.inputs.items():
                kind = {"integer": int, "float": float, "boolean": bool, "string": str}[parameter.type]
                annotation = kind if parameter.required else kind | None
                options = {"max_length": parameter.maxLength} if kind is str else {}
                fields[field] = (annotation, Field(default=... if parameter.required else None, **options))
            self.inputs[name] = create_model("OperatorInput_" + name, __config__=ConfigDict(extra="forbid", strict=True, allow_inf_nan=False), **fields)

    def catalog(self):
        resources = {}
        for name, policy in self.spec.management.resources.items():
            resource = self.spec.resources[name]
            model = self.spec.models[resource.model]
            resources[name] = {"label": policy.label or name, "titleField": policy.titleField,
                "primaryKey": next(iter(self.storage.tables[resource.model].primary_key.columns)).name,
                "fields": {field: {**model.fields[field].model_dump(), **settings.model_dump()}
                           for field, settings in policy.fields.items()}}
        return {"generation": self.generation.digest, "resources": resources,
                "actions": {name: action.model_dump(exclude={"source"}) for name, action in self.spec.management.actions.items()}}

    def table(self, resource):
        if resource not in self.spec.management.resources:
            raise AdministrationError(403, "resource_not_enabled_for_management")
        return self.storage.tables[self.spec.resources[resource].model]

    def describe(self, resource, row):
        return {"id": row[next(iter(self.table(resource).primary_key.columns)).name], "etag": etag(row, self._etag_key),
                "values": json_safe({field: row[field] for field in self.spec.management.resources[resource].fields})}

    def read(self, resource, record_id=None, offset=0, query=""):
        table = self.table(resource)
        pk = next(iter(table.primary_key.columns))
        with self.storage.sessions() as session:
            if record_id is not None:
                row = session.execute(sa.select(table).where(pk == record_id)).mappings().first()
                if row is None:
                    raise AdministrationError(404, "record_not_found")
                return self.describe(resource, row)
            statement = sa.select(table)
            if query:
                strings = [table.c[field].icontains(query, autoescape=True) for field in self.spec.management.resources[resource].fields
                           if self.spec.models[self.spec.resources[resource].model].fields[field].type == "string"]
                if query.isdecimal() and len(query) <= 19 and int(query) < 2**63:
                    strings.append(pk == int(query))
                statement = statement.where(sa.or_(*strings) if strings else sa.false())
            rows = session.execute(statement.order_by(pk).limit(26).offset(offset)).mappings().all()
            return {"records": [self.describe(resource, row) for row in rows[:25]],
                    "nextOffset": offset + 25 if len(rows) > 25 else None, "offset": offset}

    def write(self, resource, record_id, payload, action_name=None):
        request_hash = hashlib.sha256(json.dumps([resource, record_id, action_name, payload.model_dump()], sort_keys=True, allow_nan=False).encode()).hexdigest()
        events = []
        with self.storage.sessions() as session:
            try:
                if self.spec.database.backend == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    session.execute(sa.text("SET LOCAL statement_timeout = 5000"))
                    # Serialize receipt checks even for different target records.
                    lock = int.from_bytes(hashlib.sha256(b"looprd-operator-writes-v1").digest()[:8], "big", signed=True)
                    session.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
                previous = self.storage.operator_audit.existing(session, payload.operationId)
                if previous:
                    if previous["requestHash"] != request_hash:
                        raise AdministrationError(409, "operation_id_reused_with_different_input")
                    return {"operationId": payload.operationId, "duplicate": True, "changedRecords": len(previous["changes"])}, []
                if self.generation.digest != payload.generation:
                    raise AdministrationError(409, "specification_changed_reload_management_options")
                if self.storage.events:
                    self.storage.events.begin(session)
                action = self.spec.management.actions.get(action_name) if action_name else None
                if action_name and (not action or action.resource != resource):
                    raise AdministrationError(403, "action_not_enabled_for_resource")
                writes = action.writes if action else {resource: [field for field, policy in self.spec.management.resources[resource].fields.items() if policy.editable]}
                context = ActionContext(self, session, writes, record_id, payload.reason)
                target = context._row(resource, record_id)
                if etag(target, self._etag_key) != payload.etag:
                    raise AdministrationError(412, "record_changed_reload_before_editing")
                if action:
                    values = self.inputs[action_name].model_validate_json(json.dumps(payload.values)).model_dump()
                    for name, value in values.items():
                        constraints(value, action.inputs[name])
                    context.body = values
                    result = self.actions[action_name](context)
                    if result is not None:
                        if inspect.iscoroutine(result):
                            result.close()
                        raise AdministrationError(422, "management_actions_must_return_none")
                else:
                    context.update(resource, record_id, payload.values)
                changes = [{"resource": name, "id": identity, "fields": sorted(fields)}
                           for (name, identity), fields in context._changes.items()]
                self.storage.operator_audit.record(session, self.generation, payload, request_hash,
                    "action:" + action_name if action_name else "edit", resource + "/" + str(record_id), changes)
                events = context._events
                session.commit()
            except BaseException:
                session.rollback()
                raise
        return {"operationId": payload.operationId, "duplicate": False, "changedRecords": len(changes)}, events


class ActionContext:
    def __init__(self, service, session, writes, record_id, reason):
        self._service, self._session, self._writes = service, session, writes
        self._changes, self._events = {}, []
        self._updates = 0
        self._deadline = time.monotonic() + 10
        self.recordId, self.reason, self.body = record_id, reason, {}
        self.principal = Principal("management", "Looprd operator", frozenset(), kind="operator")

    def reject(self, code="action_rejected"):
        import re
        raise AdministrationError(422, code if re.fullmatch(r"[a-z_]{1,80}", code) else "action_rejected")

    def _row(self, resource, identity):
        if time.monotonic() > self._deadline:
            raise AdministrationError(408, "action_deadline_exceeded")
        if type(identity) is not int or not 0 < identity < 2**63:
            raise AdministrationError(422, "invalid_record_id")
        table = self._service.table(resource)
        row = self._session.execute(sa.select(table).where(next(iter(table.primary_key.columns)) == identity).with_for_update()).mappings().first()
        if row is None:
            raise AdministrationError(404, "record_not_found")
        return row

    def get(self, resource, identity):
        return self._service.describe(resource, self._row(resource, identity))["values"]

    def update(self, resource, identity, values):
        self._updates += 1
        if self._updates > 50:
            raise AdministrationError(422, "too_many_record_updates")
        if not isinstance(values, dict) or not values or not values.keys() <= set(self._writes.get(resource, [])):
            raise AdministrationError(403, "fields_not_enabled_for_management")
        if len(self._changes) >= 50 and (resource, identity) not in self._changes:
            raise AdministrationError(422, "too_many_changed_records")
        old = self._row(resource, identity)
        service, session = self._service, self._session
        spec_resource = service.spec.resources[resource]
        values = service.storage.inputs[spec_resource.model][1].model_validate_json(json.dumps(values, allow_nan=False)).model_dump(exclude_unset=True)
        for field, value in values.items():
            constraints(value, service.spec.management.resources[resource].fields[field])
        changed = {field for field, value in values.items() if value != old[field]}
        if not changed:
            return
        revision_field = service.storage.revisions.get(spec_resource.model)
        if revision_field:
            values[revision_field] = old[revision_field] + 1
        table = service.table(resource)
        session.execute(table.update().where(next(iter(table.primary_key.columns)) == identity).values(**values))
        new = dict(self._row(resource, identity))
        public_record = {field: new[field] for field in spec_resource.projection} if spec_resource.projection else new
        event = json_safe({"component": service.spec.component, "resource": resource, "action": "updated", "id": identity, "data": public_record})
        if service.storage.events and resource in service.spec.websocket.resources:
            event = service.storage.events.emit(session, event, json_safe(new))
        if spec_resource.policy:
            event["_looprd_record"] = json_safe(new)
        self._events.append(event)
        self._changes.setdefault((resource, identity), set()).update(changed)
