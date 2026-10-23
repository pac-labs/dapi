"""Bounded expansion, durable mounts and optional discovery of independent children."""

import copy
import json

import pytest

from looprd.component_tree import BudgetReader
from looprd.runtime import Runtime
from looprd.spec_imports import compose
from looprd.sources import SourceReader
from looprd.tree_state import validate_tree
from test_subcomponents import family, write


async def test_failed_checkpoint_does_not_publish_child_mounts(setup_project, spec_data, monkeypatch):
    settings, write_spec, _ = setup_project
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        original, save = runtime.active["demo"], runtime.save
        family(settings, write_spec, spec_data)
        def fail(*args):
            raise OSError("synthetic full disk")
        monkeypatch.setattr(runtime, "save", fail)
        assert not await runtime.reconcile()
        assert runtime.active == {"demo": original} and not runtime.tree
        monkeypatch.setattr(runtime, "save", save)
        await runtime.reconcile()
        assert "child" in runtime.active and "child" in runtime.tree
    finally:
        await runtime.close()


async def test_reparent_requires_explicit_removal(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    root, child, path = family(settings, write_spec, spec_data)
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        previous = runtime.active["child"]
        other = copy.deepcopy(child)
        other["component"] = "other"
        other["subcomponents"] = [{"source": "nested/looprd.yaml"}]
        write(path.parent / "nested/looprd.yaml", child)
        write(path, other)
        await runtime.reconcile()
        assert runtime.active["child"] is previous and "demo" in runtime.errors
        root["subcomponents"] = []
        write_spec(root)
        await runtime.reconcile()
        assert "child" not in runtime.active
        root["subcomponents"] = [{"source": "games/child/looprd.yaml"}]
        write_spec(root)
        await runtime.reconcile()
        assert runtime.active["child"].prefix == "/looprd/demo/other/child/v1"
    finally:
        await runtime.close()


@pytest.mark.parametrize("change", ["rename", "promote"])
async def test_identity_changes_cannot_leave_orphan_mounts(setup_project, spec_data, change):
    settings, write_spec, write_sources = setup_project
    _, child, path = family(settings, write_spec, spec_data)
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        previous = runtime.active["child"]
        if change == "rename":
            child["component"] = "renamed"
            write(path, child)
            await runtime.reconcile()
            assert runtime.active["child"] is previous and "renamed" not in runtime.active
        else:
            write_sources([{"id": "child", "specification": "games/child/looprd.yaml"}])
            await runtime.reconcile()
            assert not runtime.active and not runtime.tree
            await runtime.reconcile()
            assert runtime.active["child"].prefix == "/looprd/child/v1"
    finally:
        await runtime.close()


@pytest.mark.parametrize("limit", ["files", "bytes"])
async def test_shared_document_budget(limit):
    class Reader:
        async def read(self, location):
            return "x" * (1_048_576 if limit == "bytes" else 1)
    reader = BudgetReader(Reader())
    for _ in range(4 if limit == "bytes" else 256):
        await reader.read("test")
    with pytest.raises(ValueError, match="limit|4 MiB"):
        await reader.read("test")


async def test_depth_bound_leaves_healthy_ancestors(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    root = dict(spec_data, subcomponents=[{"source": "next/looprd.yaml"}])
    write_spec(root)
    folder = settings.data.parent
    for index in range(10):
        folder /= "next"
        value = dict(spec_data, component=f"child-{index}", subcomponents=[{"source": "next/looprd.yaml"}])
        write(folder / "looprd.yaml", value)
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        assert "demo" in runtime.active and runtime.errors
        assert max(len(value["namespace"]) for value in runtime.tree.values()) == 9
        assert "child-8" not in runtime.active
    finally:
        await runtime.close()


async def test_declarations_are_not_allowed_in_fragments(tmp_path, spec_data):
    write(tmp_path / "looprd.yaml", dict(spec_data, includes=["fragment.yaml"]))
    write(tmp_path / "fragment.yaml", {"subcomponents": [{"source": "child.yaml"}]})
    reader = SourceReader(str(tmp_path / "looprd.yaml"))
    try:
        with pytest.raises(ValueError, match="root specification"):
            await compose(reader, str(tmp_path / "looprd.yaml"))
    finally:
        await reader.close()


@pytest.mark.parametrize("bad", [
    {"owner": "demo", "parent": "child", "source": "a.yaml", "namespace": ["demo", "child"]},
    {"owner": "demo", "parent": "demo", "source": "../a.yaml", "namespace": ["demo", "child"]},
    {"owner": "demo", "parent": "missing", "source": "a.yaml", "namespace": ["demo", "missing", "child"]},
])
def test_invalid_checkpoint_ancestry_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_tree({"child": bad}, {"demo": {}, "child": {}})


async def test_child_discovery_uses_nested_location_and_no_false_withdrawal(setup_project, spec_data, monkeypatch):
    from discovery_receiver_fixture import ReceiverFixture
    from test_discovery import sidecar, settle
    from test_discovery_delivery import prepare
    settings, write_spec, _ = setup_project
    root, _, path = family(settings, write_spec, spec_data)
    write(path.parent / "atriumd.yaml", sidecar("child"))
    receiver = ReceiverFixture()
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    try:
        await runtime.reconcile()
        await settle(runtime)
        await delivery.tick()
        assert len(receiver.posts) == 1
        message = receiver.posts[0]
        assert message["component"] == "child"
        assert message["runtime"]["apiBaseUrl"] == "https://api.example.com/looprd/demo/child/v1"
        path.unlink()
        await runtime.reconcile()
        await settle(runtime)
        await delivery.tick()
        assert len(receiver.posts) == 1 and "child" in runtime.active
        root["subcomponents"] = []
        write_spec(root)
        await runtime.reconcile()
        await settle(runtime)
        await delivery.tick()
        assert receiver.posts[-1]["operation"] == "withdraw"
        assert receiver.posts[-1]["component"] == "child"
    finally:
        await runtime.close()
