"""Bounded, same-origin specification composition before contract validation."""

from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .spec import parse_document

IDENTITY = {"apiVersion", "component", "release", "api", "auth", "database", "realtime"}


def child_location(parent, relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 512
            or any(part in ("", ".", "..") for part in relative.split("/"))
            or any(char in relative for char in "\\:%?#") or relative.startswith("/")):
        raise ValueError("includes require plain relative descendant file paths")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in relative):
        raise ValueError("includes require plain relative descendant file paths")
    url = urlsplit(parent)
    if parent.startswith("https://"):
        child = urljoin(parent, relative)
        # The query carries immutable GitHub Contents API revisions.
        return child + ("?" + url.query if url.query else "")
    if parent.startswith("github://"):
        path = url.path.rsplit("/", 1)[0] + "/" + relative
        return f"github://{url.netloc}{path}?{url.query}"
    base = Path(parent).resolve().parent
    child = (base / relative).resolve()
    if not child.is_relative_to(base):
        raise ValueError("include escapes its containing directory")
    return str(child)


def merge(target, addition, depth=0):
    """Combine disjoint mappings and ordered lists; never override a definition."""
    if depth > 32:
        raise ValueError("specification mapping depth exceeded")
    for key, value in addition.items():
        if key not in target:
            target[key] = value
        elif isinstance(target[key], dict) and isinstance(value, dict):
            merge(target[key], value, depth + 1)
        elif isinstance(target[key], list) and isinstance(value, list):
            target[key].extend(value)
        else:
            raise ValueError("conflicting specification definitions")


async def compose(reader, location, content=None):
    seen = set()
    total = 0

    async def visit(current, supplied, depth):
        nonlocal total
        if depth > 8 or len(seen) >= 64:
            raise ValueError("specification import depth/file limit exceeded")
        canonical = current if "://" in current else str(Path(current).resolve())
        if canonical in seen:
            raise ValueError("repeated or cyclic specification import")
        seen.add(canonical)
        text = supplied if supplied is not None else await reader.read(current)
        total += len(text.encode("utf-8"))
        if total > 1_048_576:
            raise ValueError("combined specification exceeds 1 MiB")
        document = parse_document(text)
        if not isinstance(document, dict):
            raise ValueError("specification must be a mapping")
        includes = document.pop("includes", [])
        if not isinstance(includes, list) or len(includes) > 32:
            raise ValueError("includes must be a list of at most 32 paths")
        if depth and IDENTITY.intersection(document):
            raise ValueError("component identity and infrastructure belong in the root specification")
        for relative in includes:
            merge(document, await visit(child_location(current, relative), None, depth + 1))
        return document

    return await visit(location, content, 0)
