"""Bounded file/HTTPS reads; fetching failure is never an empty sources list."""

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

import httpx

from .spec import Sources, Specification, parse_document

MAX_DOCUMENT = 1_048_576


class SourceReader:
    def __init__(self, sources_location):
        self.location = sources_location
        self.base = sources_location
        self.client = httpx.AsyncClient(timeout=15, follow_redirects=False)

    async def read(self, location):
        if location.startswith("github://"):
            content, _ = await self.github_file(location)
            return content
        if location.startswith("https://"):
            headers = {}
            url = urlsplit(location)
            if url.username or url.password or url.port not in (None, 443):
                raise ValueError("URLs cannot contain credentials or custom ports")
            if url.hostname in ("api.github.com", "raw.githubusercontent.com"):
                token = await asyncio.to_thread(self.github_token)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                if url.hostname == "api.github.com":
                    headers["X-GitHub-Api-Version"] = "2022-11-28"
                    headers["Accept"] = "application/vnd.github.raw+json" if "/contents/" in url.path else "application/vnd.github+json"
            async with self.client.stream("GET", location, headers=headers) as response:
                response.raise_for_status()
                chunks, total = [], 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_DOCUMENT:
                        raise ValueError("remote document exceeds size limit")
                    chunks.append(chunk)
            return b"".join(chunks).decode("utf-8")
        if "://" in location:
            raise ValueError("only github://, HTTPS, and local paths are supported")
        return await asyncio.to_thread(self._read_local, location)

    @staticmethod
    def github_token():
        location = os.getenv("LOOPRD_GITHUB_TOKEN_FILE")
        if location:
            with Path(location).open(encoding="utf-8") as file:
                token = file.read(16_385).strip()
            if not token or len(token) > 16_384:
                raise ValueError("GitHub token file is empty or exceeds size limit")
            return token
        return os.getenv("LOOPRD_GITHUB_TOKEN")

    async def github_file(self, location):
        url = urlsplit(location)
        parts = url.path.strip("/").split("/", 1)
        query = parse_qs(url.query, keep_blank_values=True)
        if (not re.fullmatch(r"[\w.-]+", url.netloc) or len(parts) != 2
                or not re.fullmatch(r"[\w.-]+", parts[0]) or url.fragment
                or set(query) - {"ref"} or len(query.get("ref", ["main"])) != 1):
            raise ValueError("use github://OWNER/REPO/path?ref=BRANCH_OR_COMMIT")
        path = parts[1]
        if any(p in ("", ".", "..") for p in path.split("/")) or "\\" in path:
            raise ValueError("invalid GitHub content path")
        repository = f"{url.netloc}/{parts[0]}"
        revision = await self.resolve_revision(repository, query.get("ref", ["main"])[0])
        content_url = self.contents_url(repository, path, revision)
        content = await self.read(content_url)
        return content, f"github://{repository}/{path}?ref={revision}"

    async def resolve_revision(self, repository, ref):
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            return ref
        info = await self.read(f"https://api.github.com/repos/{repository}/commits/{quote(ref, safe='')}")
        revision = json.loads(info)["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("invalid resolved GitHub revision")
        return revision

    @staticmethod
    def contents_url(repository, path, revision):
        return f"https://api.github.com/repos/{repository}/contents/{quote(path, safe='/')}?{urlencode({'ref': revision})}"

    @staticmethod
    def _read_local(location):
        with Path(location).open("rb") as file:
            content = file.read(MAX_DOCUMENT + 1)
        if len(content) > MAX_DOCUMENT:
            raise ValueError("local document exceeds size limit")
        return content.decode("utf-8-sig")

    async def sources(self):
        # All entries validate before the runtime considers any removal.
        if self.location.startswith("github://"):
            content, base = await self.github_file(self.location)
        else:
            content, base = await self.read(self.location), self.location
        sources = Sources.model_validate(parse_document(content))
        self.base = base
        return sources

    async def component(self, source):
        if source.repository:
            repository = source.repository.removeprefix("https://github.com/").removesuffix(".git")
            revision = await self.resolve_revision(repository, source.ref)
            location = self.contents_url(repository, source.specification.replace("\\", "/"), revision)
        else:
            revision = "content"
            location = source.specification
            if not location.startswith(("https://", "github://")):
                if self.base.startswith("github://"):
                    url = urlsplit(self.base)
                    relative = urljoin("https://github.local" + url.path, location)
                    location = f"github://{url.netloc}{urlsplit(relative).path}?{url.query}"
                elif self.base.startswith("https://"):
                    location = urljoin(self.base, location)
                elif not Path(location).is_absolute():
                    location = str(Path(self.base).parent / location)
        spec = Specification.model_validate(parse_document(await self.read(location)))
        if spec.component != source.id:
            raise ValueError("specification component differs from source ID")
        return spec, revision

    async def close(self):
        await self.client.aclose()
