"""Small same-origin management panel; credentials stay in page memory."""

import secrets

from starlette.responses import HTMLResponse

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Looprd management</title><style nonce="NONCE">
body{font:16px system-ui;background:#101b24;color:#e5edf3;margin:0 auto;padding:32px;max-width:960px}
h1{margin-bottom:8px}section{background:#192a36;padding:20px;border-radius:12px;margin:20px 0}
input,select,button,textarea{font:inherit;padding:10px;border-radius:6px;border:1px solid #618091;margin:6px 4px 6px 0;max-width:100%;box-sizing:border-box}
input,textarea,select{background:#101b24;color:#fff}button{background:#a2dfc5;color:#10251b;cursor:pointer}
label{display:block;margin-top:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}textarea{width:100%}
.muted{color:#b8cbd6}#message{min-height:24px;color:#f8d18b}li{margin:12px 0;overflow-wrap:anywhere}
[hidden]{display:none!important}
</style></head><body>
<h1>Looprd</h1><p class="muted">Components, source reconciliation, and API keys.</p>
<section><form id="login"><label for="token">Management API key</label>
<input id="token" type="password" autocomplete="off" required size="55" placeholder="Paste the key from your container terminal">
<button>Connect</button><button type="button" id="logout">Disconnect</button></form>
<p class="muted">The key stays in this page's memory. Reloading or disconnecting clears it.</p></section>
<p id="message" role="status" aria-live="polite"></p>
<main id="controls" hidden><section><h2>Components</h2>
<button id="refresh">Refresh status</button><button id="reconcile">Reconcile sources</button><pre id="status"></pre></section>
<section><h2>Component API keys</h2><label for="component">Component using database authentication</label>
<select id="component"></select><button id="list">List keys</button><p id="available" class="muted"></p>
<form id="create"><label for="label">Key label</label><input id="label" required maxlength="100">
<label for="scopes">Granted scopes (comma separated)</label><input id="scopes" required size="55" placeholder="items:read, events:read">
<label for="expiry">Expires after seconds</label><input id="expiry" type="number" min="1" max="31536000" value="2592000" required>
<button>Create key</button></form>
<div id="issued" hidden><label for="new-key">New API key — copy now; shown only once</label>
<textarea id="new-key" readonly rows="3" spellcheck="false"></textarea><button id="clear-key">Clear key</button></div>
<ul id="keys"></ul></section></main>
<script nonce="NONCE">
const el=id=>document.getElementById(id);let token='',components={};
function clearKey(){el('new-key').value='';el('issued').hidden=true;}
function disconnect(){token='';el('token').value='';el('controls').hidden=true;el('status').textContent='';el('keys').replaceChildren();clearKey();}
async function api(path,method='GET',body){
 const response=await fetch('/_looprd/'+path,{method,cache:'no-store',credentials:'omit',redirect:'error',
  headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 if(!response.ok){if(response.status===401)disconnect();let detail;try{detail=await response.json();}catch{}throw Error(detail?.error||'Request failed ('+response.status+')');}
 return response.status===204?null:response.json();
}
function run(action){el('message').textContent='Working…';Promise.resolve().then(action).then(()=>el('message').textContent='Ready.').catch(e=>el('message').textContent=e.message);}
function selected(){return encodeURIComponent(el('component').value);}
function available(){const item=components[el('component').value];el('available').textContent='Available scopes: '+(item?.scopes||[]).join(', ');el('keys').replaceChildren();clearKey();}
async function status(){const result=await api('status');components=result.components;el('status').textContent=JSON.stringify(result,null,2);
 const previous=el('component').value;el('component').replaceChildren();for(const [id,item] of Object.entries(components)){
  if(item.authMode==='database'){const option=document.createElement('option');option.value=id;option.textContent=id;el('component').append(option);}}
 if(components[previous]?.authMode==='database')el('component').value=previous;available();el('controls').hidden=false;
}
async function list(){if(!selected())throw Error('Select a component with database authentication.');
 const result=await api('components/'+selected()+'/keys');el('keys').replaceChildren();
 for(const key of result.keys){const li=document.createElement('li');const state=key.revokedAt?'revoked':key.expiresAt&&key.expiresAt*1000<=Date.now()?'expired':'active';
  li.textContent=key.label+' — '+key.scopes.join(', ')+' — '+state+' — '+key.id+' ';
  if(!key.revokedAt){const button=document.createElement('button');button.textContent='Revoke';const component=selected();
   button.onclick=()=>run(async()=>{await api('components/'+component+'/keys/'+encodeURIComponent(key.id),'DELETE');clearKey();await list();});li.append(button);}
  el('keys').append(li);}
}
el('login').onsubmit=e=>{e.preventDefault();token=el('token').value.trim();el('token').value='';run(status);};
el('logout').onclick=()=>{disconnect();el('message').textContent='Disconnected.';};
el('refresh').onclick=()=>run(status);el('reconcile').onclick=()=>run(async()=>{await api('reconcile','POST');await status();});
el('component').onchange=available;el('list').onclick=()=>run(list);el('clear-key').onclick=clearKey;
el('create').onsubmit=e=>{e.preventDefault();run(async()=>{if(!selected())throw Error('Select a component.');clearKey();
 const key=await api('components/'+selected()+'/keys','POST',{label:el('label').value,scopes:el('scopes').value.split(',').map(s=>s.trim()).filter(Boolean),expiresInSeconds:Number(el('expiry').value)});
 el('new-key').value=key.apiKey;el('issued').hidden=false;await list();});};
window.addEventListener('pagehide',disconnect);
</script></body></html>"""


async def panel(request):
    nonce = secrets.token_urlsafe(24)
    return HTMLResponse(PAGE.replace("NONCE", nonce), headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                                   "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'",
    })
