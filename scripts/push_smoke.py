"""Real ntfy + built Looprd integration; all containers/data are disposable."""

import subprocess
import sys
import uuid

NTFY = "docker.io/binwiederhier/ntfy:v2.31.0"

PROBE = r'''
import base64, json, time
from pathlib import Path
import httpx
from starlette.testclient import TestClient
from looprd.app import create_app
from looprd.config import Settings

root=Path('/tmp/push-smoke');root.mkdir();(root/'secrets').mkdir()
(root/'secrets/admin-token').write_text('smoke-admin')
spec={'apiVersion':'looprd/v1','component':'push-smoke','release':'1.0','api':{'version':'v1'},
 'auth':{'mode':'database','scopes':['data','push']},'database':{'backend':'sqlite'},
 'realtime':{'delivery':'durable'},'push':{'scopes':['push']},
 'models':{'Item':{'fields':{'id':{'type':'integer','primaryKey':True,'generated':True},'name':{'type':'string','maxLength':100}}}},
 'resources':{'items':{'model':'Item','path':'/items','operations':['list','create','delete'],
 'permissions':{'list':['data'],'create':['data'],'delete':['data']}}},
 'websocket':{'resources':['items'],'permissions':{'items':['data']}}}
(root/'looprd.json').write_text(json.dumps(spec))
(root/'sources.json').write_text(json.dumps({'apiVersion':'looprd/v1','components':[{'id':'push-smoke','specification':'looprd.json'}]}))
settings=Settings(sources=str(root/'sources.json'),data=root/'data',secrets=root/'secrets',poll_seconds=3600,drain_seconds=0,
 push_public_url='https://push.example.com',push_delivery_url='http://push-provider:8080')
admin={'Authorization':'Bearer smoke-admin'};base='/looprd/push-smoke/v1'
def messages():
 with httpx.Client(timeout=5,trust_env=False) as provider:
  response=provider.get('http://push-provider:8080/up_smoke/json?poll=1&since=all',auth=('smoke','smoke-test-password'))
  response.raise_for_status()
  result=[]
  for line in response.text.splitlines():
   message=json.loads(line)
   if message.get('event')!='message':continue
   raw=message['message']
   if message.get('encoding')=='base64':raw=base64.b64decode(raw)
   result.append(json.loads(raw))
  return result
def wait(kind):
 for attempt in range(80):
  found=[message for message in messages() if message['type']==kind]
  if found:return found[-1]
  time.sleep(.25)
 raise AssertionError('ntfy did not deliver expected '+kind)
with TestClient(create_app(settings)) as client:
 generation=client.app.state.runtime.active['push-smoke']
 grant=generation.storage.keys.create('smoke',['data','push'],3600)
 headers={'Authorization':'Bearer '+grant['apiKey']}
 response=client.post(base+'/_push/devices',headers=headers,json={'installationId':'smoke_device','endpoint':'https://push.example.com/up_smoke'})
 assert response.status_code==202,response.text
 device=response.json();proof=wait('verify')
 assert client.post(base+'/_push/devices/'+device['id']+'/verify',headers=headers,json={'challenge':proof['challenge']}).status_code==200
 baseline=client.get(base+'/_sync/items',headers=headers).json()['cursor']
 principal=client.portal.call(generation.authenticate,headers['Authorization'])
 with generation.storage.events.transaction() as session:
  generation.storage.events.emit(session,{'resource':'items','action':'created','id':1,'data':{'id':1,'name':'ready'}})
  generation.storage.push.notify(session,principal.id)
 assert wait('sync')=={'apiVersion':'looprd-push/v1','type':'sync'}
 events=client.get(base+'/_events/items',headers=headers,params={'after':baseline}).json()['events']
 assert events[-1]['data']['name']=='ready'
 assert client.get('/_looprd/components/push-smoke/push',headers=admin).json()['devices']==1
 with httpx.Client(timeout=5,trust_env=False) as provider:
  assert provider.get('http://push-provider:8080/up_smoke/json?poll=1').status_code==403
 assert client.delete(base+'/_push/devices/'+device['id'],headers=headers).status_code==200
print('Built image + real ntfy: endpoint proof, binary wake delivery, authenticated HTTP recovery and private topic reads passed')
'''


def main():
    engine, image = sys.argv[1:]
    suffix = uuid.uuid4().hex[:12]
    network, provider = "push-smoke-" + suffix, "ntfy-smoke-" + suffix
    def run(*arguments, **kwargs):
        return subprocess.run([engine, *arguments], check=True, **kwargs)
    run("pull", NTFY)
    run("network", "create", "--internal", network, stdout=subprocess.DEVNULL)
    try:
        run("run", "-d", "--name", provider, "--network", network, "--network-alias", "push-provider",
            "--read-only", "--user", "10001:10001", "--cap-drop=all", "--security-opt=no-new-privileges",
            "--tmpfs", "/var/lib/ntfy:rw,mode=1777,size=32m", "--tmpfs", "/tmp:rw,size=16m",
            "-e", "NTFY_LISTEN_HTTP=:8080", "-e", "NTFY_AUTH_FILE=/var/lib/ntfy/auth.db",
            "-e", "NTFY_CACHE_FILE=/var/lib/ntfy/cache.db", "-e", "NTFY_AUTH_DEFAULT_ACCESS=deny-all",
            NTFY, "serve", stdout=subprocess.DEVNULL)
        run("exec", "-e", "NTFY_PASSWORD=smoke-test-password", provider, "ntfy", "user", "add", "smoke")
        run("exec", provider, "ntfy", "access", "smoke", "up*", "read-only")
        run("exec", provider, "ntfy", "access", "everyone", "up*", "write-only")
        run("run", "--rm", "-i", "--network", network, "--read-only", "--cap-drop=all",
            "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,size=64m", "--entrypoint", "python",
            image, "-", input=PROBE, text=True)
    finally:
        subprocess.run([engine, "rm", "-f", provider], stdout=subprocess.DEVNULL, check=False)
        subprocess.run([engine, "network", "rm", network], stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
