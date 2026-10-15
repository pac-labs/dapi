"""Generic owner/membership predicates shared by CRUD, live events and replay."""

import sqlalchemy as sa

from .outbox import EventError


class AccessPolicies:
    def __init__(self, storage):
        self.storage = storage

    def predicate(self, resource_name, principal):
        resource = self.storage.spec.resources[resource_name]
        policy = resource.policy
        if not policy:
            return sa.true()
        table = self.storage.tables[resource.model]
        conditions = []
        if policy.ownerField:
            conditions.append(table.c[policy.ownerField] == principal.id)
        if policy.membership:
            member = policy.membership
            memberships = self.storage.tables[member.model]
            conditions.append(sa.exists(sa.select(1).select_from(memberships).where(
                memberships.c[member.subjectField] == principal.id,
                memberships.c[member.membershipField] == table.c[member.recordField])))
        return sa.and_(*conditions)

    def can_record(self, session, resource_name, principal, record):
        policy = self.storage.spec.resources[resource_name].policy
        if not policy:
            return True
        if not record or policy.ownerField and record.get(policy.ownerField) != principal.id:
            return False
        if policy.membership:
            member = policy.membership
            value = record.get(member.recordField)
            if value is None:
                return False
            table = self.storage.tables[member.model]
            return session.execute(sa.select(table.c[member.subjectField]).where(
                table.c[member.subjectField] == principal.id, table.c[member.membershipField] == value).limit(1)).first() is not None
        return True

    def create(self, session, resource_name, principal, values):
        policy = self.storage.spec.resources[resource_name].policy
        if policy and policy.ownerField:
            if policy.ownerField in values and values[policy.ownerField] != principal.id:
                raise EventError(403, "cannot assign another principal as owner")
            values = {**values, policy.ownerField: principal.id}
        if not self.can_record(session, resource_name, principal, values):
            raise EventError(403, "record access denied")
        return values

    def update(self, session, resource_name, principal, record, values):
        policy = self.storage.spec.resources[resource_name].policy
        if policy and policy.ownerField and policy.ownerField in values:
            raise EventError(403, "owner field is immutable")
        if not self.can_record(session, resource_name, principal, {**record, **values}):
            raise EventError(403, "updated record access denied")

    def can_event(self, session, resource_name, principal, record):
        resource = self.storage.spec.resources[resource_name]
        if not resource.policy:
            return True
        if not self.can_record(session, resource_name, principal, record):
            return False
        table = self.storage.tables[resource.model]
        primary = next(iter(table.primary_key.columns))
        if record.get(primary.name) is None:
            return False
        current = session.execute(sa.select(table).where(primary == record[primary.name])).mappings().first()
        return current is None or self.can_record(session, resource_name, principal, current)

    def event(self, resource_name, principal, record):
        with self.storage.sessions() as session:
            return self.can_event(session, resource_name, principal, record)
