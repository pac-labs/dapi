"""Preserve legacy fingerprints while validating opt-in SQL constraints."""

import sqlalchemy as sa


def model_signature(model):
    fields = {}
    for name, column in model.fields.items():
        value = column.model_dump()
        if column.references is None:
            value.pop("references")
        fields[name] = value
    result = {"fields": fields}
    if model.indexes:
        result["indexes"] = [index.model_dump() for index in model.indexes]
    if model.unique:
        result["unique"] = model.unique
    return result


def validate_constraints(connection, spec):
    inspector = sa.inspect(connection)
    for name, model in spec.models.items():
        indexes = {index["name"]: index for index in inspector.get_indexes(name)}
        unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)}
        unique.update(tuple(index["column_names"]) for index in indexes.values() if index["unique"])
        if any(tuple(fields) not in unique for fields in model.unique):
            raise ValueError("declared composite uniqueness is missing")
        for index in model.indexes:
            actual = indexes.get(index.name)
            if not actual or actual["column_names"] != index.fields or bool(actual["unique"]) != index.unique:
                raise ValueError("declared index is missing or incompatible")
        foreign = inspector.get_foreign_keys(name)
        for field, column in model.fields.items():
            if column.references and not any(
                key["constrained_columns"] == [field] and key["referred_table"] == column.references.model
                and key["referred_columns"] == [column.references.field]
                and key.get("options", {}).get("ondelete") in (None, "RESTRICT", "NO ACTION") for key in foreign):
                raise ValueError("declared foreign key is missing")
