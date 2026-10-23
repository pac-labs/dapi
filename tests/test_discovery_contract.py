"""Shared receiver schemas and immutable examples must match runtime validation."""

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from looprd.discovery_contract import Observation, Receiver, Acknowledgement

ROOT = Path(__file__).parents[1] / "contracts/atrium-discovery-v2"


@pytest.mark.parametrize("name", ["upsert-git", "upsert-local", "upsert-https", "withdraw-https"])
def test_shared_observation_examples(name):
    schema = json.loads((ROOT / "observation.schema.json").read_text())
    document = json.loads((ROOT / (name + ".json")).read_text())
    jsonschema.Draft202012Validator(schema).validate(document)
    Observation.model_validate(document)


def test_schemas_reject_fabricated_revisions_and_mutated_fingerprints():
    schema = json.loads((ROOT / "observation.schema.json").read_text())
    document = json.loads((ROOT / "upsert-local.json").read_text())
    changed = copy.deepcopy(document); changed["source"]["revision"] = "a" * 40
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(changed, schema)
    with pytest.raises(ValueError): Observation.model_validate(changed)
    changed = copy.deepcopy(document); del changed["source"]["revision"]
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(changed, schema)
    with pytest.raises(ValueError): Observation.model_validate(changed)
    changed = copy.deepcopy(document); changed["sidecar"]["game"]["title"] = "Mutated"
    with pytest.raises(ValueError): Observation.model_validate(changed)


@pytest.mark.parametrize("name,model", [("capabilities", Receiver), ("ack", Acknowledgement)])
def test_shared_response_examples(name, model):
    schema = json.loads((ROOT / (name + ".schema.json")).read_text())
    document = json.loads((ROOT / (name + ".json")).read_text())
    jsonschema.validate(document, schema)
    model.model_validate(document)
