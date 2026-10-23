"""Validate private checkpoint ancestry before restoring child mounts."""

import re

from .spec_imports import child_location


def validate_tree(tree, components):
    if not isinstance(tree, dict) or len(tree) > 10000:
        raise ValueError("invalid subcomponent checkpoint")
    for key, value in tree.items():
        if (key not in components or not isinstance(value, dict)
                or set(value) != {"owner", "parent", "source", "namespace"}):
            raise ValueError("invalid subcomponent checkpoint")
        namespace = value["namespace"]
        if (not isinstance(namespace, list) or not 2 <= len(namespace) <= 9
                or any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", item)
                       for item in namespace) or len(set(namespace)) != len(namespace)
                or namespace[-1] != key or namespace[-2] != value["parent"]
                or namespace[0] != value["owner"] or value["owner"] in tree
                or value["owner"] not in components):
            raise ValueError("invalid subcomponent ancestry")
        parent = tree.get(value["parent"])
        if parent and parent["namespace"] != namespace[:-1]:
            raise ValueError("inconsistent subcomponent ancestry")
        if not parent and len(namespace) != 2:
            raise ValueError("missing subcomponent parent")
        child_location("https://example.invalid/looprd.yaml", value["source"])
    return tree
