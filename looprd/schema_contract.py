"""Preserve legacy fingerprints while validating opt-in SQL constraints."""

import sqlalchemy as sa


def partial_index(index):
    return any(key.endswith("_where") and value is not None
               for key, value in index.get("dialect_options", {}).items())


def unique_keys(connection, name, inspector=None):
    """Read enforced full-column uniqueness, including SQLite's implicit indexes."""
    if connection.dialect.name == "sqlite":
        quote = connection.dialect.identifier_preparer.quote
        indexes = connection.exec_driver_sql("PRAGMA index_list(" + quote(name) + ")").mappings().all()
        result = set()
        for index in indexes:
            if not index["unique"] or index["partial"]:
                continue
            columns = tuple(row["name"] for row in connection.exec_driver_sql(
                "PRAGMA index_info(" + quote(index["name"]) + ")").mappings())
            # Expressions have no column name and do not declare this column contract.
            if columns and all(column is not None for column in columns):
                result.add(columns)
        return result
    inspector = inspector or sa.inspect(connection)
    result = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)}
    result.update(tuple(index["column_names"]) for index in inspector.get_indexes(name)
                  if index["unique"] and not partial_index(index) and all(index["column_names"]))
    return result


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
        unique = unique_keys(connection, name, inspector)
        if any(tuple(fields) not in unique for fields in model.unique):
            raise ValueError("declared composite uniqueness is missing")
        for index in model.indexes:
            actual = indexes.get(index.name)
            if not actual or partial_index(actual) or actual["column_names"] != index.fields or bool(actual["unique"]) != index.unique:
                raise ValueError("declared index is missing or incompatible")
        foreign = inspector.get_foreign_keys(name)
        for field, column in model.fields.items():
            if column.references and not any(
                key["constrained_columns"] == [field] and key["referred_table"] == column.references.model
                and key["referred_columns"] == [column.references.field]
                and key.get("options", {}).get("ondelete") in (None, "RESTRICT", "NO ACTION") for key in foreign):
                raise ValueError("declared foreign key is missing")
