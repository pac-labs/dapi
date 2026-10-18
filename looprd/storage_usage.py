"""Bounded read-only data-volume accounting; filenames and paths stay private."""

import asyncio
import os
import shutil
import stat
import time


def measure(root, components, *, max_entries=20000, max_seconds=2):
    started = time.monotonic()
    capacity = shutil.disk_usage(root)
    groups, seen, pending = {}, set(), [(root, ())]
    entries = skipped = 0
    partial = False
    allocated_supported = hasattr(root.stat(), "st_blocks")
    while pending and not partial:
        directory, parts = pending.pop()
        try:
            with os.scandir(directory) as children:
                for child in children:
                    entries += 1
                    if entries > max_entries or time.monotonic() - started > max_seconds:
                        partial = True
                        break
                    try:
                        value = child.stat(follow_symlinks=False)
                        if os.name == "nt" and not value.st_ino:
                            # Windows DirEntry.stat omits inode IDs; os.stat resolves hardlink identity.
                            value = os.stat(child.path, follow_symlinks=False)
                        if stat.S_ISLNK(value.st_mode):
                            skipped += 1
                            continue
                        path = parts + (child.name,)
                        if stat.S_ISDIR(value.st_mode):
                            pending.append((child.path, path))
                        elif stat.S_ISREG(value.st_mode):
                            identity = (value.st_dev, value.st_ino)
                            if value.st_ino and identity in seen:
                                continue
                            seen.add(identity)
                            group = category(path, components)
                            row = groups.setdefault(group, {"category": group[0], "component": group[1],
                                "bytes": 0, "allocatedBytes": 0 if allocated_supported else None, "files": 0})
                            row["bytes"] += value.st_size
                            if allocated_supported:
                                row["allocatedBytes"] += value.st_blocks * 512
                            row["files"] += 1
                    except OSError:
                        skipped += 1
        except OSError:
            skipped += 1
    percent = round(capacity.used / capacity.total * 100, 1) if capacity.total else 0
    return {"measuredAt": time.time(), "state": "partial" if partial or skipped else "complete",
        "scannedEntries": min(entries, max_entries), "skippedEntries": skipped,
        "filesystem": {"totalBytes": capacity.total, "usedBytes": capacity.used,
            "freeBytes": capacity.free, "usedPercent": percent,
            "level": "critical" if percent >= 90 or capacity.free < 1024**3 else "warning" if percent >= 80 else "normal"},
        "categories": sorted(groups.values(), key=lambda row: (-row["bytes"], row["category"], row["component"] or "")),
        "measuredBytes": sum(row["bytes"] for row in groups.values()),
        "allocatedBytes": sum(row["allocatedBytes"] for row in groups.values()) if allocated_supported else None}


def category(parts, components):
    if parts[0] == "files":
        return "Temporary files", None
    if parts[0] == "databases":
        name = parts[1].split(".", 1)[0] if len(parts) > 1 else ""
        return "Component databases", name if name in components else None
    if len(parts) == 1:
        name = parts[0].split(".", 1)[0]
        labels = {"operations": "Telemetry and audit", "bugreports": "Bug reports",
            "diagnostics": "Diagnostics", "discovery": "Discovery observations"}
        if name in labels:
            return labels[name], None
    return "Runtime state and other data", None


class StorageUsage:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.result = None
        self.sampled = 0
        self.components = None

    async def read(self, settings, components):
        async with self.lock:
            if self.result is None or time.monotonic() - self.sampled >= 30 or components != self.components:
                work = asyncio.create_task(asyncio.to_thread(measure, settings.data, components))
                try:
                    self.result = await asyncio.shield(work)
                except asyncio.CancelledError:
                    await work
                    raise
                self.sampled = time.monotonic()
                self.components = components
            return {**self.result, "ageSeconds": int(time.monotonic() - self.sampled), "cacheSeconds": 30}
