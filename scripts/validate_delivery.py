"""Offline source-package identity and design checks; no install, network or deployment."""

import hashlib
import json
import tomllib
from pathlib import Path


def validate(root):
    board = json.loads((root / ".ziprunner-workboard-update.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "ziprunner-manifest.json").read_text(encoding="utf-8"))
    recipe = json.loads((root / "project.zr").read_text(encoding="utf-8"))
    assert board["project"] == manifest["project"]["key"] == recipe["project_key"] == "Looprd-DAPI"
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert board["version"] == manifest["project"]["version"] == version
    assert board["implemented"] and isinstance(board["remaining"], list)
    assert not recipe["start"] and not recipe["stop"]
    assert manifest["release"]["requires_operator_approval"] is True
    assert manifest["release"]["requested"] is False
    plan = (root / "PROJECT_PLAN.adoc").read_text(encoding="utf-8")
    design = (root / "DESIGN.adoc").read_text(encoding="utf-8")
    assert hashlib.sha256(design.encode()).hexdigest() in plan
    for name in ("Containerfile", "requirements.lock", "package-lock.json", "ui/panel.scss",
                 "looprd/runtime.py", "looprd/discovery_delivery.py", "tests/test_discovery_delivery.py",
                 "contracts/atrium-discovery-v2/observation.schema.json"):
        assert (root / name).is_file(), "Complete source missing " + name
    return True


if __name__ == "__main__":
    validate(Path(__file__).resolve().parents[1])
    print("Complete DAPI source identity and design checks passed; no release/deployment authorized.")
