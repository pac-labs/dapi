"""Exercise the shipped Traefik path router against the built Looprd image."""

import subprocess
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

if __package__:
    from .files_smoke import smoke_files
else:
    from files_smoke import smoke_files


def router_host(content):
    """Read the single literal Host matcher supported by this deployment smoke test."""
    rules = re.findall(r"^\s*rule:\s*(.+)$", content, flags=re.MULTILINE)
    if len(rules) != 1:
        raise ValueError("smoke configuration must declare exactly one router rule")
    hosts = re.findall(r"\bHost\(\s*`([^`]+)`\s*\)", rules[0])
    if len(hosts) != 1 or not re.fullmatch(r"[A-Za-z0-9.-]+", hosts[0]):
        raise ValueError("smoke router requires exactly one literal Host matcher")
    return hosts[0]


def check_proxy(engine, command, looprd_name, directory, root):
    network, proxy = looprd_name + "-proxy-net", looprd_name + "-traefik"
    dynamic = Path(directory) / "dynamic"
    dynamic.mkdir(mode=0o755)
    content = (root / "deploy/traefik/looprd.yml").read_text()
    configured_host = router_host(content)
    content = content.replace("http://127.0.0.1:8080", f"http://{looprd_name}:8080")
    # Test host/path routing over an internal HTTP test entry point. Production retains TLS.
    content = content.replace("tls: {}", "")
    (dynamic / "looprd.yml").write_text(content)
    command("network", "create", network)
    try:
        command("network", "connect", network, looprd_name)
        command("run", "--detach", "--name", proxy, "--network", network,
                "--publish", "127.0.0.1::8080", "--volume", f"{dynamic}:/etc/traefik/dynamic:ro",
                "traefik:v3", "--entrypoints.websecure.address=:8080",
                "--providers.file.directory=/etc/traefik/dynamic", "--providers.file.watch=true")
        address = command("port", proxy, "8080/tcp").splitlines()[0]

        def status(path, host=configured_host):
            request = urllib.request.Request("http://" + address + path, headers={
                "Host": host, "Authorization": "Bearer smoke-token",
            })
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if status("/looprd/roboscope/v1/scans") == 200:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError("Traefik did not route the Looprd API")
        assert status("/dapi/roboscope/v1/scans") == 200
        assert status("/looprd/roboscope/v1/scans", "wrong." + configured_host) == 404
        assert status("/_looprd/status") == 404
        assert status("/looprdx/roboscope/v1/scans") == 404
        assert status("/") == 404
        direct = "http://" + command("port", looprd_name, "8080/tcp").splitlines()[0]
        smoke_files(direct, "http://" + address, configured_host)
        print("Traefik smoke passed: host/path routing, prefix preservation, management exclusion")
    except BaseException:
        subprocess.run([engine, "logs", proxy], check=False)
        raise
    finally:
        subprocess.run([engine, "rm", "--force", proxy], check=False, capture_output=True)
        subprocess.run([engine, "network", "disconnect", network, looprd_name], check=False, capture_output=True)
        subprocess.run([engine, "network", "rm", network], check=False, capture_output=True)
