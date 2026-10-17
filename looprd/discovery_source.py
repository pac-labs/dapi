"""Optional sibling observations pinned to a successfully loaded root source."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .atrium_sidecar import validate
from .spec import parse_document

MAX_SIDECAR = 65536


@dataclass(frozen=True)
class SourceSnapshot:
    location: str
    revision: str
    digest: str

    @property
    def identity(self):
        return hashlib.sha256(self.location.encode()).hexdigest()


def origin(value):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.port not in (None, 443) or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("discovery origin must be plain HTTPS")
    return urlunsplit((url.scheme, url.netloc, "", "", ""))


def locations(snapshot, settings):
    url = urlsplit(snapshot.location)
    if snapshot.location.startswith("github://"):
        from .sources import SourceReader
        repository, path = url.netloc + "/" + url.path.strip("/").split("/", 1)[0], url.path.strip("/").split("/", 1)[1]
        directory = path.rpartition("/")[0]
        return (SourceReader.contents_url(repository, (directory + "/" if directory else "") + "atriumd.yaml", snapshot.revision),
                SourceReader.contents_url(repository, directory, snapshot.revision))
    if snapshot.location.startswith("https://"):
        sibling = url.path.rsplit("/", 1)[0] + "/atriumd.yaml"
        if url.hostname == "api.github.com" and "/contents/" in url.path and snapshot.revision != "content":
            return urlunsplit(url._replace(path=sibling)), urlunsplit(url._replace(path=sibling.rsplit("/", 1)[0]))
        if urlunsplit((url.scheme, url.netloc, "", "", "")) not in settings.discovery_https_origins:
            raise ValueError("discovery HTTPS origin not approved")
        return urlunsplit(url._replace(path=sibling)), None
    directory = Path(snapshot.location).resolve().parent
    candidate = directory / "atriumd.yaml"
    if not candidate.resolve().is_relative_to(directory) or candidate.is_symlink() and not candidate.exists():
        raise ValueError("sidecar escapes source directory")
    return str(candidate), None


async def confirmed_missing(reader, directory):
    # Contents listings cap at 1000 entries. At the cap absence is inconclusive.
    listing = json.loads(await reader.read(directory))
    return (isinstance(listing, list) and len(listing) < 1000
            and all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in listing)
            and not any(item["name"] == "atriumd.yaml" for item in listing))


async def observe(reader, snapshot, component, settings):
    directory = None
    try:
        location, directory = locations(snapshot, settings)
        try:
            content = await reader.read(location, max_bytes=MAX_SIDECAR)
        except FileNotFoundError:
            return {"state": "absent"}
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404:
                raise
            if directory and not await confirmed_missing(reader, directory):
                return {"state": "unavailable", "reason": "absence_unconfirmed"}
            return {"state": "absent"}
    except (OSError, ValueError, httpx.HTTPError):
        return {"state": "unavailable", "reason": "source_unavailable"}
    try:
        document = validate(parse_document(content), component)
        raw = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode()) > MAX_SIDECAR:
            raise ValueError("sidecar too large")
        fingerprint = hashlib.sha256(json.dumps([snapshot.identity, snapshot.revision, snapshot.digest, raw]).encode()).hexdigest()
        return {"state": "valid", "sidecar": document, "fingerprint": fingerprint}
    except Exception:
        return {"state": "invalid", "reason": "metadata_invalid"}
