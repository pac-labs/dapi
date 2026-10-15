"""Declared scalar filters, stable keyset ordering and signed query cursors."""

import base64
import hashlib
import hmac
import json
import math
import secrets
from datetime import datetime

import sqlalchemy as sa

from .outbox import EventError


class Queries:
    def __init__(self, storage):
        self.storage = storage
        self.key = sa.Table("_looprd_query_key_v1", sa.MetaData(),
            sa.Column("id", sa.Integer, primary_key=True), sa.Column("secret", sa.String(64), nullable=False))

    def initialize(self, connection):
        self.key.create(connection, checkfirst=True)
        if connection.execute(sa.select(self.key.c.id)).first() is None:
            connection.execute(self.key.insert().values(id=1, secret=secrets.token_hex(32)))

    @staticmethod
    def value(field, text):
        if len(text) > 4096:
            raise ValueError("filter value too long")
        try:
            value = json.loads(text)
        except ValueError:
            value = text
        if field.type == "string":
            return value if isinstance(value, str) else text
        if field.type == "datetime":
            return datetime.fromisoformat(str(value))
        expected = {"string": str, "integer": int, "boolean": bool, "float": (int, float)}[field.type]
        if not isinstance(value, expected) or isinstance(value, bool) and field.type != "boolean":
            raise ValueError("filter type mismatch")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("filter numbers must be finite")
        return value

    def list(self, session, name, principal, params, limit, offset):
        resource = self.storage.spec.resources[name]
        table, model = self.storage.tables[resource.model], self.storage.spec.models[resource.model]
        primary = next(iter(table.primary_key.columns)).name
        conditions = [self.storage.policies.predicate(name, principal)]
        for key, text in params.items():
            if key in ("limit", "offset", "sort", "pagination", "cursor"):
                continue
            parts = key.split(".")
            if len(parts) != 3 or parts[0] != "filter":
                raise ValueError("unknown query option")
            _, field, operation = parts
            if operation not in resource.query.filters.get(field, []):
                raise ValueError("undeclared filter")
            column = table.c[field]
            if operation == "in":
                values = json.loads(text)
                if not isinstance(values, list) or not 1 <= len(values) <= 50:
                    raise ValueError("in requires 1-50 values")
                conditions.append(column.in_([self.value(model.fields[field], json.dumps(value)) for value in values]))
            else:
                value = self.value(model.fields[field], text)
                conditions.append({"eq": column.__eq__, "ne": column.__ne__, "lt": column.__lt__,
                    "le": column.__le__, "gt": column.__gt__, "ge": column.__ge__,
                    "prefix": lambda value: column.startswith(value, autoescape=True)}[operation](value))
        sort = params.get("sort", primary).split(",")
        if not 1 <= len(sort) <= 3 or len(set(item.lstrip("-") for item in sort)) != len(sort):
            raise ValueError("sort requires 1-3 unique fields")
        if any(item.lstrip("-") not in set(resource.query.sort) | {primary} for item in sort):
            raise ValueError("undeclared sort")
        if primary not in [item.lstrip("-") for item in sort]:
            sort.append(primary)
        paging = "cursor" in params or params.get("pagination") == "cursor"
        if params.get("pagination") not in (None, "cursor") or paging and offset:
            raise ValueError("cursor and offset pagination cannot be mixed")
        signature = hashlib.sha256(json.dumps({"principal": principal.id, "resource": name,
            "query": resource.model_dump(), "sort": sort,
            "filters": {key: value for key, value in params.items() if key.startswith("filter.")}}, sort_keys=True).encode()).digest()
        secret = session.execute(sa.select(self.key.c.secret)).scalar_one().encode()
        if params.get("cursor"):
            token = params["cursor"]
            try:
                if len(token) > 4096:
                    raise ValueError()
                raw, mac = token.rsplit(".", 1)
                if not hmac.compare_digest(hmac.new(secret, signature + raw.encode(), hashlib.sha256).hexdigest(), mac):
                    raise ValueError()
                values = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
                if not isinstance(values, list) or len(values) != len(sort):
                    raise ValueError()
                equality, continuation = [], []
                for item, value in zip(sort, values):
                    field = item.lstrip("-")
                    value = self.value(model.fields[field], json.dumps(value))
                    column = table.c[field]
                    continuation.append(sa.and_(*equality, column < value if item.startswith("-") else column > value))
                    equality.append(column == value)
                conditions.append(sa.or_(*continuation))
            except (ValueError, UnicodeError, TypeError):
                raise EventError(400, "invalid query cursor") from None
        order = [table.c[item[1:]].desc() if item.startswith("-") else table.c[item].asc() for item in sort]
        rows = [dict(row) for row in session.execute(sa.select(table).where(*conditions)
                .order_by(*order).limit(limit + int(paging)).offset(offset)).mappings()]
        more = paging and len(rows) > limit
        rows = rows[:limit]
        cursor = None
        if more:
            values = [rows[-1][item.lstrip("-")] for item in sort]
            raw = base64.urlsafe_b64encode(json.dumps(values, default=lambda value: value.isoformat()).encode()).decode().rstrip("=")
            cursor = raw + "." + hmac.new(secret, signature + raw.encode(), hashlib.sha256).hexdigest()
        if resource.projection:
            rows = [{field: row[field] for field in resource.projection} for row in rows]
        return {"items": rows, "cursor": cursor, "hasMore": bool(more)} if paging else rows
