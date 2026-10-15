"""Cross-reference checks for optional data/service descriptors."""


def data_contracts(spec):
    from .shapes import check_shape
    index_names = set()
    revisions = {}
    for name, model in spec.models.items():
        for column in model.fields.values():
            if column.references:
                ref = column.references
                target = spec.models.get(ref.model)
                if (not target or ref.field not in target.fields or not target.fields[ref.field].primaryKey
                        or column.type != target.fields[ref.field].type or column.primaryKey):
                    raise ValueError("foreign key must reference a compatible declared primary key")
        for fields in [index.fields for index in model.indexes] + model.unique:
            if not fields or len(fields) != len(set(fields)) or not set(fields) <= model.fields.keys():
                raise ValueError("index/unique fields must be nonempty, unique and declared")
        for index in model.indexes:
            if index.name in index_names:
                raise ValueError("index names must be unique across component models")
            index_names.add(index.name)
    for resource in spec.resources.values():
        model = spec.models.get(resource.model)
        if not model:
            continue  # Core reference validation reports this.
        if resource.projection and (not set(resource.projection) <= model.fields.keys()
                                    or set(resource.operations) - {"get", "list"}):
            raise ValueError("projections are declared-field read-only resources")
        if resource.query:
            for field in set(resource.query.filters) | set(resource.query.sort):
                if field not in model.fields or model.fields[field].type == "json":
                    raise ValueError("query fields must reference declared scalar fields")
            for field in resource.query.sort:
                if not (model.fields[field].required or model.fields[field].primaryKey):
                    raise ValueError("keyset sort fields must be non-nullable")
                if model.fields[field].type == "string" and model.fields[field].maxLength > 512:
                    raise ValueError("keyset string sort fields are limited to 512 characters")
            for field, operations in resource.query.filters.items():
                if not operations or len(set(operations)) != len(operations):
                    raise ValueError("filter operators must be nonempty and unique")
                if "prefix" in operations and model.fields[field].type != "string":
                    raise ValueError("prefix filtering requires a string field")
                if model.fields[field].type == "boolean" and set(operations) - {"eq", "ne", "in"}:
                    raise ValueError("boolean filters support eq, ne and in")
        if resource.revisionField:
            column = model.fields.get(resource.revisionField)
            if not column or column.type != "integer" or not column.required or column.primaryKey:
                raise ValueError("revision field must be a required non-key integer")
            previous = revisions.setdefault(resource.model, resource.revisionField)
            if previous != resource.revisionField:
                raise ValueError("a model has only one revision field")
    for resource in spec.resources.values():
        if resource.model in revisions and set(resource.operations) - {"get", "list"} and resource.revisionField != revisions[resource.model]:
            raise ValueError("all writable resources for a versioned model must use its revision field")
    contracts = [(handler.contract.name, handler.contract.version) for handler in spec.handlers if handler.contract]
    for handler in spec.handlers:
        if handler.contract:
            if handler.contract.serviceOnly and spec.auth.mode not in ("database", "verified"):
                raise ValueError("service-only contracts require individual machine identity verification")
            check_shape(handler.contract.request)
            check_shape(handler.contract.response)
    if len(set(contracts)) != len(contracts):
        raise ValueError("duplicate service contract")
    dependencies = [(item.component, item.contract) for item in spec.dependencies]
    if len(set(dependencies)) != len(dependencies) or any(item.component == spec.component for item in spec.dependencies):
        raise ValueError("dependencies must be unique and refer to other components")
