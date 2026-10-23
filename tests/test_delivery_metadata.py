"""Source ZIP is project-bound and cannot approve its own deployment."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("validate_delivery", ROOT / "scripts/validate_delivery.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def test_source_metadata_identity_and_design():
    assert validation.validate(ROOT)
