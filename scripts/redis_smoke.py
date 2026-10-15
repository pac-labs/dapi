"""Built-image Redis companion/fanout smoke; creates only disposable resources."""

import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


PROBE = r'''
import asyncio, json, sys
import httpx
from websockets.asyncio.client import connect

async def main():
    peer, mode = sys.argv[1:]
    headers = {'Authorization': 'Bearer smoke-token'}
    admin = {'Authorization': 'Bearer admin'}
    base = '/looprd/redis-demo/v1'
    async with httpx.AsyncClient(timeout=5) as client:
        async with asyncio.timeout(30):
            while True:
                statuses = []
                for host in ['localhost', peer]:
                    try:
                        r = await client.get('http://' + host + ':8080/_looprd/status', headers=admin)
                        statuses.append(r.json()['components']['redis-demo']['realtime']['state'])
                    except (httpx.HTTPError, KeyError):
                        statuses.append('starting')
                if mode == 'outage' or statuses == ['connected', 'connected']:
                    break
                await asyncio.sleep(0.2)
        target = peer if mode == 'fanout' else 'localhost'
        async with connect('ws://' + target + ':8080' + base + '/events', additional_headers=headers) as ws:
            await ws.send(json.dumps({'subscribe': 'items'}))
            assert json.loads(await asyncio.wait_for(ws.recv(), 5)) == {'subscribed': 'items'}
            response = await client.post('http://localhost:8080' + base + '/items',
                                         headers=headers, json={'name': mode})
            assert response.status_code == 201, response.text
            event = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert event['resource'] == 'items' and event['data']['name'] == mode
            try:
                await asyncio.wait_for(ws.recv(), 0.25)
                raise AssertionError('duplicate notification')
            except TimeoutError:
                pass
            # Authorization remains at the receiving runtime.
            blocked = await client.put('http://' + target + ':8080/_looprd/components/redis-demo/availability',
                                       headers=admin, json={'blocked': True})
            assert blocked.status_code == 200
            from websockets.exceptions import ConnectionClosed
            try:
                await asyncio.wait_for(ws.recv(), 5)
                raise AssertionError('blocked socket stayed open')
            except ConnectionClosed as error:
                assert error.code == 1008
            await client.put('http://' + target + ':8080/_looprd/components/redis-demo/availability',
                             headers=admin, json={'blocked': False})
        if mode == 'outage':
            async with asyncio.timeout(15):
                while True:
                    response = await client.get('http://localhost:8080/_looprd/status', headers=admin)
                    if response.json()['components']['redis-demo']['realtime']['state'] == 'degraded':
                        break
                    await asyncio.sleep(0.2)
asyncio.run(main())
'''


def main():
    engine, image = sys.argv[1:3]
    prefix = "looprd-redis-smoke-" + uuid.uuid4().hex[:8]
    network, server, left, right = [prefix + suffix for suffix in ("-net", "-redis", "-left", "-right")]

    def command(*args, **kwargs):
        try:
            return subprocess.run([engine, *args], check=True, capture_output=True, text=True, **kwargs).stdout
        except subprocess.CalledProcessError as error:
            print(error.stdout, error.stderr, file=sys.stderr)
            raise

    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory)
        config.chmod(0o755)
        (config / "secrets/redis-demo").mkdir(parents=True)
        (config / "secrets/redis-demo/api-token").write_text("smoke-token")
        (config / "secrets/admin-token").write_text("admin")
        (config / "secrets/redis-url").write_text(f"redis://looprd:smoke-only@{server}:6379/0")
        digest = hashlib.sha256(b"smoke-only").hexdigest()
        (config / "redis.acl").write_text("user default off\nuser looprd on #" + digest +
            " &smoke:* +ping +publish +subscribe +unsubscribe +echo +client|setinfo +client|setname\n")
        root = Path(__file__).resolve().parents[1]
        (config / "looprd.yaml").write_bytes((root / "examples/redis-events.yaml").read_bytes())
        (config / "sources.yaml").write_text(json.dumps({"apiVersion": "looprd/v1", "components": [
            {"id": "redis-demo", "specification": "looprd.yaml"}]}))
        command("network", "create", "--internal", network)
        try:
            command("run", "--detach", "--name", server, "--network", network,
                    "--read-only", "--memory", "192m", "--cap-drop", "all", "--user", "999:999",
                    "--volume", f"{config}/redis.acl:/etc/redis/users.acl:ro",
                    "redis:7.4-alpine", "redis-server", "--bind", "0.0.0.0", "--save", "", "--appendonly", "no",
                    "--maxmemory", "128mb", "--maxmemory-policy", "noeviction", "--aclfile", "/etc/redis/users.acl")
            for name in (left, right):
                command("run", "--detach", "--name", name, "--network", network, "--read-only",
                        "--tmpfs", "/tmp:rw,size=16m", "--tmpfs", "/var/lib/looprd:rw,size=32m,uid=10001,gid=10001",
                        "--volume", f"{config}:/etc/looprd:ro", "--env", "LOOPRD_EVENT_NAMESPACE=smoke", image)
            command("exec", "-i", left, "python", "-", right, "fanout", input=PROBE)
            command("stop", server)
            command("exec", "-i", left, "python", "-", right, "outage", input=PROBE)
            print("Redis companion smoke passed: ACL, cross-runtime fanout, no echo, API blocking, local delivery during outage")
        finally:
            for name in (left, right, server):
                subprocess.run([engine, "rm", "--force", name], check=False, capture_output=True)
            subprocess.run([engine, "network", "rm", network], check=False, capture_output=True)


if __name__ == "__main__":
    main()
