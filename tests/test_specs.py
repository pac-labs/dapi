import pytest
from pydantic import ValidationError

from looprd.spec import Sources, Specification, parse_document


def test_duplicate_yaml_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        parse_document("components: []\ncomponents: []")


@pytest.mark.parametrize("change", [
    lambda s: s.update(unknown=True),
    lambda s: s["resources"]["items"].update(model="Missing"),
    lambda s: s["auth"].pop("secretRef"),
    lambda s: s["models"]["Item"]["fields"]["id"].update(generated=False),
    lambda s: s["websocket"].update(resources=["missing"]),
    lambda s: s["resources"]["items"].update(path="/_ws-ticket"),
    lambda s: s["handlers"].append({"path": "/items/other", "response": {}}),
])
def test_invalid_contract(spec_data, change):
    spec_data["handlers"] = []
    change(spec_data)
    with pytest.raises(ValidationError):
        Specification.model_validate(spec_data)


def test_destructive_migration_rejected(spec_data):
    spec_data["database"].update(schemaVersion=2, migrations=[{"version": 2, "statements": ["DROP TABLE Item"]}])
    with pytest.raises(ValidationError, match="additive"):
        Specification.model_validate(spec_data)


def test_quoted_postgresql_migration_identifiers(spec_data):
    spec_data["database"].update(schemaVersion=2, migrations=[{
        "version": 2, "statements": ['ALTER TABLE "Item" ADD COLUMN extra BOOLEAN'],
    }])
    assert Specification.model_validate(spec_data).database.schemaVersion == 2


def test_duplicate_sources_rejected():
    with pytest.raises(ValidationError):
        Sources.model_validate({"apiVersion": "looprd/v1", "components": [{"id": "same"}, {"id": "same"}]})


def test_repository_path_traversal_rejected():
    with pytest.raises(ValidationError):
        Sources.model_validate({"apiVersion": "looprd/v1", "components": [{
            "id": "demo", "repository": "https://github.com/example/demo.git", "specification": "../secret",
        }]})
