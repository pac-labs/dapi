"""Exercise the built image, including mount replacement and retained data.

Usage: python scripts/container_smoke.py [docker|podman] [image] [--traefik]
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def main():
    engine = sys.argv[1] if len(sys.argv) > 1 else "docker"
    image = sys.argv[2] if len(sys.argv) > 2 else "looprd:smoke"
    name = "looprd-smoke-" + uuid.uuid4().hex[:10]
    volume = name + "-data"

    def command(*args, **kwargs):
        return subprocess.run([engine, *args], check=True, capture_output=True, text=True, **kwargs).stdout.strip()

    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config"
        config.mkdir(mode=0o755)
        (config / "secrets/roboscope").mkdir(parents=True)
        (config / "secrets/roboscope/api-token").write_text("smoke-token")
        (config / "secrets/admin-token").write_text("admin")
        root = Path(__file__).resolve().parents[1]
        shutil.copy(root / "examples/looprd.yaml", config / "looprd.yaml")
        sources_content = (root / "examples/sources.yaml").read_text()
        sources = config / "sources.yaml"
        sources.write_text(sources_content)
        command("volume", "create", volume)
        try:
            command("run", "--detach", "--name", name, "--read-only", "--tmpfs", "/tmp:rw,size=64m",
                    "--publish", "127.0.0.1::8080", "--volume", f"{config}:/etc/looprd:ro",
                    "--volume", f"{volume}:/var/lib/looprd", "--env", "LOOPRD_POLL_SECONDS=1",
                    "--env", "LOOPRD_DRAIN_SECONDS=0", image)
            address = command("port", name, "8080/tcp").splitlines()[0]
            base = "http://" + address

            def request(path, method="GET", body=None):
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(base + path, data=data, method=method, headers={
                    "Authorization": "Bearer smoke-token", "Content-Type": "application/json",
                })
                try:
                    with urllib.request.urlopen(req, timeout=3) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)

            def wait_for(path, status):
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if request(path)[0] == status:
                            return
                    except OSError:
                        pass
                    time.sleep(0.2)
                raise AssertionError(f"{path} did not return {status}")

            path = "/looprd/roboscope/v1/scans"
            wait_for(path, 200)
            status, record = request(path, "POST", {"name": "retained", "status": "new"})
            assert status == 201
            sources.rename(config / "sources.offline")
            time.sleep(2)
            assert request(path)[0] == 200, "unreachable sources stopped the component"
            sources.write_text("apiVersion: looprd/v1\ncomponents: []\n")
            wait_for(path, 404)
            # Replace the inode, exercising directory mount semantics.
            replacement = config / "sources.replacement"
            replacement.write_text(sources_content)
            replacement.replace(sources)
            wait_for(path, 200)
            assert request(path + "/" + str(record["id"]))[1]["name"] == "retained"
            command("restart", name)
            # Engines may assign a different ephemeral host port after restart.
            address = command("port", name, "8080/tcp").splitlines()[0]
            base = "http://" + address
            wait_for(path, 200)
            assert request(path + "/" + str(record["id"]))[1]["name"] == "retained"
            print("Container smoke passed: serving, unreachable sources, removal, re-addition, restart, retained data")
            if "--traefik" in sys.argv[3:]:
                from traefik_smoke import check_proxy
                check_proxy(engine, command, name, directory, root)
        except BaseException:
            subprocess.run([engine, "logs", name], check=False)
            raise
        finally:
            subprocess.run([engine, "rm", "--force", name], check=False, capture_output=True)
            subprocess.run([engine, "volume", "rm", volume], check=False, capture_output=True)


if __name__ == "__main__":
    main()
