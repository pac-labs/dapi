"""Host-only Podman sampler. No container control endpoint or credentials exported."""

import argparse
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

NAMES = ("looprd", "looprd-postgres", "looprd-redis", "looprd-ntfy")
STATES = {"running", "exited", "paused", "created", "stopped"}


def command(arguments):
    result = subprocess.run(["podman", *arguments], capture_output=True, text=True, timeout=8)
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError("container measurement unavailable")
    return result.stdout


def size(text):
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)\s*", text)
    if not match:
        return None
    units = {"B": 1, "kB": 1000, "KB": 1000, "KiB": 1024, "MB": 10**6, "MiB": 2**20,
             "GB": 10**9, "GiB": 2**30, "TB": 10**12, "TiB": 2**40}
    value = float(match[1]) * units[match[2]]
    return int(value) if math.isfinite(value) and value <= 10**22 else None


def pair(text):
    parts = str(text).split("/")
    return [size(part) for part in parts] if len(parts) == 2 else [None, None]


def duration(text):
    units = {"h": 3600, "m": 60, "s": 1, "ms": .001, "us": .000001, "µs": .000001, "ns": .000000001}
    parts = re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|us|µs|ns|h|m|s)", str(text))
    if not parts or "".join(value + unit for value, unit in parts) != text:
        return None
    value = sum(float(value) * units[unit] for value, unit in parts)
    return value if math.isfinite(value) else None


def collect(previous=None, runner=command, memory_supported=None, now=None):
    now = time.time() if now is None else now
    if memory_supported is None:
        controllers = Path("/sys/fs/cgroup/cgroup.controllers")
        memory_supported = controllers.exists() and "memory" in controllers.read_text().split()
    rows = []
    for name in NAMES:
        row = {"name": name, "state": "unknown"}
        try:
            state, started, restarts = runner(["container", "inspect", "--format",
                "{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}", name]).strip().split("|")
            row.update(state=state if state in STATES else "unknown", startedAt=started[:64], restarts=int(restarts))
        except (ValueError, OSError, subprocess.SubprocessError):
            row["error"] = "inspect-unavailable"
        rows.append(row)
    active = [row["name"] for row in rows if row["state"] == "running"]
    try:
        stats = json.loads(runner(["stats", "--no-stream", "--format", "json", *active])) if active else []
        if not isinstance(stats, list) or len(stats) > len(NAMES):
            raise ValueError("unexpected measurements")
    except (ValueError, OSError, subprocess.SubprocessError):
        stats = []
    previous = previous if isinstance(previous, dict) else {}
    previous_rows = previous.get("containers", [])
    old = {item["name"]: item for item in previous_rows if isinstance(item, dict) and item.get("name") in NAMES} if isinstance(previous_rows, list) else {}
    for row in rows:
        values = next((item for item in stats if isinstance(item, dict) and item.get("name") == row["name"]), None)
        if not values:
            if row["state"] == "running":
                row["error"] = "stats-unavailable"
            continue
        total = duration(values.get("cpu_time", ""))
        before = old.get(row["name"], {})
        sampled = previous.get("collectedAt", now)
        elapsed = now - sampled if type(sampled) in (int, float) else 0
        cpu = None
        if (total is not None and type(before.get("cpuTotalSeconds")) in (int, float) and 0 < elapsed <= 60
                and before.get("startedAt") == row["startedAt"] and total >= before["cpuTotalSeconds"]):
            cpu = (total - before["cpuTotalSeconds"]) / elapsed * 100
        memory, limit = pair(values.get("mem_usage", "")) if memory_supported else (None, None)
        rx, tx = pair(values.get("net_io", ""))
        read, written = pair(values.get("block_io", ""))
        pids = str(values.get("pids", ""))
        row.update(cpuPercent=cpu, cpuTotalSeconds=total, memoryBytes=memory, memoryLimitBytes=limit,
            networkRxBytes=rx, networkTxBytes=tx, blockReadBytes=read, blockWriteBytes=written,
            pids=int(pids) if pids.isdecimal() else None)
    return {"version": 1, "collectedAt": now, "memorySupported": memory_supported, "containers": rows}


def collect_storage(runner=command, disk_runner=subprocess.run):
    """Host-only allocated sizes; no paths, listings or arbitrary volume names exported."""
    rows = []
    for name in ("looprd-data", "looprd-postgres-data", "looprd-redis-data", "looprd-ntfy-data"):
        row = {"name": name, "state": "unavailable", "allocatedBytes": None}
        try:
            raw = runner(["volume", "inspect", "--format", "{{.Mountpoint}}", name]).strip()
            path = Path(raw)
            if not path.is_absolute() or not path.is_dir() or path.is_symlink():
                raise ValueError("volume mount unavailable")
            result = disk_runner(["du", "-s", "-B1", "--", str(path)], capture_output=True, text=True, timeout=3)
            if result.returncode or len(result.stdout) > 4096:
                raise ValueError("volume measurement unavailable")
            value = result.stdout.split()[0]
            if not value.isdecimal():
                raise ValueError("invalid volume size")
            row.update(state="available", allocatedBytes=int(value))
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--storage", action="store_true", help="Measure only the four named Looprd volumes")
    args = parser.parse_args()
    previous = None
    try:
        with args.output.open("rb") as source:
            previous = json.loads(source.read(65536))
    except (ValueError, OSError):
        pass
    snapshot = collect(previous)
    if args.storage:
        snapshot["storage"] = collect_storage()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False, encoding="utf-8") as target:
            temporary = Path(target.name)
            json.dump(snapshot, target, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o644)
        temporary.replace(args.output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


if __name__ == "__main__":
    main()
