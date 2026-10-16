"""Explicit operator grants; application concepts remain in component specifications."""

from typing import Literal

from pydantic import Field, model_validator

from .contract import Strict, Name, Symbol


class FieldPolicy(Strict):
    label: str = Field(default="", max_length=80)
    minimum: float | None = None
    maximum: float | None = None
    choices: list[str | int | float | bool | None] | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounds(self):
        import math
        if any(value is not None and not math.isfinite(value) for value in (self.minimum, self.maximum)):
            raise ValueError("management bounds must be finite")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("management bounds are reversed")
        if self.choices and any(type(value) is float and not math.isfinite(value) for value in self.choices):
            raise ValueError("management choices must be finite")
        return self


class ManagedField(FieldPolicy):
    editable: bool = False


class ManagedResource(Strict):
    label: str = Field(default="", max_length=80)
    titleField: Symbol | None = None
    fields: dict[Symbol, ManagedField] = Field(min_length=1, max_length=32)


class ActionInput(FieldPolicy):
    type: Literal["string", "integer", "float", "boolean"] = "string"
    required: bool = True
    maxLength: int = Field(default=200, ge=1, le=2000)


class ManagementAction(Strict):
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    resource: Name
    inputs: dict[Symbol, ActionInput] = Field(default_factory=dict, max_length=16)
    writes: dict[Name, list[Symbol]] = Field(min_length=1, max_length=16)
    source: str = Field(min_length=1, max_length=500000)


class Management(Strict):
    resources: dict[Name, ManagedResource] = Field(min_length=1, max_length=32)
    actions: dict[Name, ManagementAction] = Field(default_factory=dict, max_length=32)


def check_management(spec):
    if spec.management is None:
        return
    if not spec.database:
        raise ValueError("data administration requires a component database")
    for name, resource in spec.management.resources.items():
        if name not in spec.resources:
            raise ValueError("unknown managed resource")
        model = spec.models.get(spec.resources[name].model)
        if model is None:
            raise ValueError("unknown managed resource model")
        fields = model.fields
        if not resource.fields.keys() <= fields.keys() or resource.titleField and resource.titleField not in resource.fields:
            raise ValueError("managed fields/title must reference declared model fields")
        for field, policy in resource.fields.items():
            if policy.editable:
                writable(spec, name, field)
            if (policy.minimum is not None or policy.maximum is not None) and fields[field].type not in ("integer", "float"):
                raise ValueError("numeric bounds require numeric managed fields")
            check_choices(policy, fields[field].type, fields[field].required or fields[field].primaryKey, fields[field].maxLength)
    for action in spec.management.actions.values():
        if action.resource not in spec.management.resources:
            raise ValueError("action target must be a managed resource")
        for name, fields in action.writes.items():
            if name not in spec.management.resources or not fields or len(fields) != len(set(fields)):
                raise ValueError("actions require explicit managed writable fields")
            for field in fields:
                if field not in spec.management.resources[name].fields:
                    raise ValueError("action fields must be exposed by management")
                writable(spec, name, field)
        for parameter in action.inputs.values():
            if (parameter.minimum is not None or parameter.maximum is not None) and parameter.type not in ("integer", "float"):
                raise ValueError("numeric action bounds require numeric input")
            check_choices(parameter, parameter.type, parameter.required, parameter.maxLength)


def writable(spec, name, field):
    resource = spec.resources[name]
    column = spec.models[resource.model].fields[field]
    protected = any(other.model == resource.model and (other.revisionField == field or other.policy and other.policy.ownerField == field)
                    for other in spec.resources.values())
    if column.generated or column.primaryKey or protected:
        raise ValueError("generated, ownership and revision fields cannot be edited")


def check_choices(policy, kind, required, maximum_length):
    for value in policy.choices or []:
        valid = value is None and not required or type(value) is {"string": str, "integer": int, "boolean": bool}.get(kind)
        valid = valid or kind == "float" and type(value) in (int, float)
        if not valid or kind == "string" and value is not None and len(value) > maximum_length:
            raise ValueError("management choices must match the declared field/input type")
