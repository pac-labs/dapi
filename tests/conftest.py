import pytest
import yaml

from looprd.config import Settings


@pytest.fixture
def spec_data():
    return {
        "apiVersion": "looprd/v1", "component": "demo", "release": "1.0",
        "api": {"version": "v1"}, "auth": {"mode": "bearer", "secretRef": "demo/token"},
        "database": {"backend": "sqlite", "schemaVersion": 1},
        "models": {"Item": {"fields": {
            "id": {"type": "integer", "primaryKey": True, "generated": True},
            "name": {"type": "string", "maxLength": 30, "required": True, "unique": True},
        }}},
        "resources": {"items": {"model": "Item", "path": "/items",
                                "operations": ["list", "get", "create", "update", "delete"]}},
        "websocket": {"path": "/events", "resources": ["items"]},
    }


@pytest.fixture
def setup_project(tmp_path, spec_data):
    settings = Settings(sources=str(tmp_path / "sources.yaml"), data=tmp_path / "data",
                        secrets=tmp_path / "secrets", poll_seconds=3600, drain_seconds=0)
    (settings.secrets / "demo").mkdir(parents=True)
    (settings.secrets / "demo/token").write_text("test-token", encoding="utf-8")
    (settings.secrets / "admin-token").write_text("admin", encoding="utf-8")

    def write_spec(data=None):
        (tmp_path / "looprd.yaml").write_text(yaml.safe_dump(data or spec_data), encoding="utf-8")

    def write_sources(entries=None):
        if entries is None:
            entries = [{"id": "demo", "specification": "looprd.yaml"}]
        (tmp_path / "sources.yaml").write_text(yaml.safe_dump({"apiVersion": "looprd/v1", "components": entries}), encoding="utf-8")

    write_spec()
    write_sources()
    return settings, write_spec, write_sources
