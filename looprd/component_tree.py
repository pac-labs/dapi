"""Expand root manifests into independent generations, retaining failed branches."""

import hashlib
import re

from .discovery_source import SourceSnapshot
from .spec import Specification
from .spec_imports import child_location, compose


class TreeConflict(ValueError):
    pass


class BudgetReader:
    """Bound all child documents and their includes across one source tree."""

    def __init__(self, reader):
        self.reader, self.files, self.total = reader, 0, 0

    async def read(self, location):
        self.files += 1
        if self.files > 256:
            raise ValueError("subcomponent document limit exceeded")
        text = await self.reader.read(location)
        self.total += len(text.encode("utf-8"))
        if self.total > 4_194_304:
            raise ValueError("subcomponent tree exceeds 4 MiB")
        return text


async def collect(runtime, sources):
    """Read before building. Duplicate deployment identities reject their source groups."""
    previous = runtime.tree
    specs, revisions, tree, errors, snapshots = {}, {}, {}, {}, {}
    reserved = {entry.id for entry in sources.components}
    groups = []

    for entry in sources.components:
        if not entry.enabled:
            continue
        group, metadata, failures, pins = {}, {}, {}, {}
        budget = BudgetReader(runtime.reader)

        def retain(component, namespace=None):
            if component not in runtime.active:
                return
            group[component] = (runtime.active[component].spec, runtime.revisions[component])
            if component in previous:
                metadata[component] = dict(previous[component])
                if namespace:
                    metadata[component]["namespace"] = namespace
            for key, value in previous.items():
                if value["parent"] == component:
                    retain(key, (namespace + [key]) if namespace else None)

        async def visit(spec, revision, location, namespace, depth):
            if depth > 8 or len(group) >= 64:
                raise ValueError("subcomponent depth/component limit exceeded")
            if spec.component in group:
                raise TreeConflict("duplicate subcomponent identity")
            group[spec.component] = (spec, revision)
            for declaration in spec.subcomponents:
                # Invalid/unavailable files retain only the prior branch for this exact edge.
                old = [key for key, value in previous.items()
                       if value["parent"] == spec.component and value["source"] == declaration.source]
                before = set(group)
                try:
                    child = child_location(location, declaration.source)
                    value = Specification.model_validate(await compose(budget, child))
                    if old and value.component not in old:
                        raise TreeConflict("remove the previous child before changing its identity")
                    child_namespace = namespace + [value.component]
                    if re.fullmatch(r"v[1-9][0-9]*", value.component):
                        raise TreeConflict("child identity conflicts with a version segment")
                    if value.component in group or value.component in reserved:
                        raise TreeConflict("duplicate deployment component identity")
                    current = runtime.active.get(value.component)
                    if (current and current.prefix.rsplit("/", 1)[0]
                            != "/looprd/" + "/".join(child_namespace)):
                        raise TreeConflict("remove an existing child before reparenting")
                    metadata[value.component] = {"owner": entry.id, "parent": spec.component,
                        "source": declaration.source, "namespace": child_namespace}
                    await visit(value, revision, child, child_namespace, depth + 1)
                    pins[value.component] = SourceSnapshot(child, revision,
                        hashlib.sha256(value.model_dump_json().encode()).hexdigest())
                except TreeConflict:
                    raise
                except Exception as error:
                    # Roll back a partially expanded branch before restoring last good children.
                    for key in set(group) - before:
                        group.pop(key, None)
                        metadata.pop(key, None)
                        pins.pop(key, None)
                    for key in set(metadata) - group.keys():
                        metadata.pop(key, None)
                    failures[spec.component] = error
                    for key in old:
                        retain(key, namespace + [key])

        old_snapshots = dict(getattr(runtime.reader, "snapshots", {}))
        try:
            if entry.id in previous:
                raise TreeConflict("remove an existing child before promoting it to root")
            spec, revision = await runtime.reader.component(entry)
            snapshot = getattr(runtime.reader, "snapshots", {}).get(entry.id)
            if spec.subcomponents and snapshot is None:
                raise ValueError("subcomponents require a source snapshot")
            if snapshot:
                pins[entry.id] = snapshot
            await visit(spec, revision, snapshot.location if snapshot else "", [entry.id], 0)
            if len(group) > 64:
                raise ValueError("subcomponent component limit exceeded")
        except Exception as error:
            group.clear()
            metadata.clear()
            pins.clear()
            failures[entry.id] = error
            if entry.id not in previous:
                retain(entry.id)
        if hasattr(runtime.reader, "snapshots"):
            runtime.reader.snapshots = old_snapshots
        groups.append((entry, group, metadata, failures, pins))

    # IDs remain stable storage/auth/service identities across the whole deployment.
    owners = {}
    conflicting = set()
    for entry, group, metadata, _, _ in groups:
        for component in group:
            old_owner = previous.get(component, {}).get("owner")
            if (component != entry.id and component in reserved
                    or old_owner and old_owner != entry.id and old_owner in reserved):
                conflicting.add(entry.id)
            if component in owners and owners[component] != entry.id:
                conflicting.update((owners[component], entry.id))
            owners[component] = entry.id
    for entry, group, metadata, failures, pins in groups:
        if entry.id in conflicting:
            group = {key: (value.spec, runtime.revisions[key]) for key, value in runtime.active.items()
                     if key == entry.id and key not in previous
                     or previous.get(key, {}).get("owner") == entry.id}
            metadata = {key: previous[key] for key in group if key in previous}
            pins = {}
            failures[entry.id] = ValueError("duplicate deployment component identity")
        for component, (spec, revision) in group.items():
            specs[component], revisions[component] = spec, revision
        tree.update(metadata)
        errors.update(failures)
        snapshots.update(pins)
    if hasattr(runtime.reader, "snapshots"):
        runtime.reader.snapshots.update(snapshots)
    return specs, revisions, tree, errors


def prefix(component, spec, tree):
    namespace = tree.get(component, {}).get("namespace", [component])
    return "/looprd/" + "/".join(namespace) + "/" + spec.api.version
