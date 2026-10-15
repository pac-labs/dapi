'use strict';
const el=id=>document.getElementById(id);
let token='',snapshot=null,selectedId='',busy=false,session=0;
const text=(id,value)=>{el(id).textContent=value;};
function node(tag,value,cls){const n=document.createElement(tag);if(value!==undefined)n.textContent=value;if(cls)n.className=cls;return n;}
function badge(target,label,state=''){target.textContent=label;target.className='badge cds--tag cds--tag--gray '+state;}
function date(value){return value?new Date(value).toLocaleString(): 'Not yet';}
function duration(seconds){if(seconds<60)return seconds+'s';if(seconds<3600)return Math.floor(seconds/60)+'m';return Math.floor(seconds/3600)+'h '+Math.floor(seconds%3600/60)+'m';}
function fact(target,label,value,mono=false){const dl=node('dl',undefined,'fact');dl.append(node('dt',label),node('dd',value??'—',mono?'mono':''));target.append(dl);}
function clearKey(){el('new-key').value='';el('issued').hidden=true;}
function disconnect(){
 clearFiles();
 session++;token='';snapshot=null;selectedId='';el('token').value='';
 for(const id of ['controls','actions','logout'])el(id).hidden=true;
 el('token').hidden=false;el('connect').hidden=false;el('token').value=rememberedKey;
 for(const id of ['status','component-list','routes','models','keys','detail-facts','runtime-facts'])el(id).replaceChildren();
 el('component').replaceChildren();el('login-card').hidden=false;showView('overview');clearKey();badge(el('connection'),'Disconnected');
}
async function api(path,method='GET',body){
 const current=session;
 const response=await fetch('/_looprd/'+path,{method,cache:'no-store',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(20000),
  headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 if(current!==session)throw Error('Session ended.');
 let result=null;if(response.status!==204){try{result=await response.json();}catch{}}
 if(current!==session)throw Error('Session ended.');
 if(!response.ok){if(response.status===401){if(token===rememberedKey)forgetCredential();disconnect();}
  if(result?.components&&token)render(result);
  throw Error(response.status===401?'Management key was rejected. Connect again.':result?.sourcesError||result?.error||'Request failed ('+response.status+')');}
 if(response.status!==204&&!result)throw Error('The server returned an invalid response.');
 return result;
}
async function run(action,quiet=false){
 if(busy)return;busy=true;
 document.querySelectorAll('[data-action]').forEach(button=>button.disabled=true);
 if(!quiet){text('message','Working…');el('message').className='';}
 try{await action();text('message',storageUnavailable?'Status updated; key is kept for this session only because browser storage is unavailable.':'Status updated.');el('message').className='';}
 catch(error){text('message',error.message);el('message').className='error';if(token)badge(el('connection'),'Refresh failed','warn');}
 finally{busy=false;document.querySelectorAll('[data-action]').forEach(button=>button.disabled=false);}
}
function ids(){return [...new Set([...Object.keys(snapshot.components),...Object.keys(snapshot.sources||{}),...Object.keys(snapshot.errors),...(snapshot.blocked||[])])].sort();}
function state(id){if(snapshot.blocked?.includes(id))return ['Blocked','warn'];if(snapshot.errors[id])return ['Needs attention','warn'];if(snapshot.components[id])return ['Active','good'];
 if(snapshot.sources?.[id]?.enabled===false)return ['Disabled',''];return ['Not loaded','warn'];}
function renderList(){
 const list=el('component-list');list.replaceChildren();
 const all=ids();let count=0;
 for(const id of all){const [label,tone]=state(id),filter=el('filter').value;
  if(!id.toLowerCase().includes(el('search').value.toLowerCase())||(filter==='active'&&(!snapshot.components[id]||snapshot.blocked?.includes(id)))||(filter==='blocked'&&label!=='Blocked')||
     (filter==='attention'&&tone!=='warn')||(filter==='disabled'&&label!=='Disabled'))continue;
  count++;const button=node('button',undefined,'component-item'+(id===selectedId?' selected':''));
  button.setAttribute('aria-pressed',String(id===selectedId));button.append(node('strong',id));
  const indicator=node('span');badge(indicator,label,tone);button.append(indicator);
  button.append(node('span',snapshot.components[id]?'Release '+snapshot.components[id].release:label==='Disabled'?'Disabled in sources · data retained':'Waiting for a working specification','small muted'));
  button.onclick=()=>{selectedId=id;renderList();renderDetail();syncPicker();showView('components');};list.append(button);
 }
 if(!count)list.append(node('p',all.length?'No matching components.':'No components registered. Add a repository to your private sources file.','empty'));
 text('component-count',all.length);
}
function renderModels(item){
 const picker=el('model-picker'),previous=picker.value,names=Object.keys(item?.models||{});
 picker.replaceChildren();for(const name of names){const option=node('option',name);option.value=name;picker.append(option);}
 if(names.includes(previous))picker.value=previous;picker.hidden=!names.length;
 el('models').replaceChildren();
 if(!names.length){el('models').append(node('p','No database models declared.','muted small'));return;}
 const table=node('table',undefined,'cds--data-table cds--layout--size-sm'),head=node('thead'),header=node('tr');
 for(const value of ['Field','Type','Constraints']){const th=node('th',value);th.scope='col';header.append(th);}head.append(header);table.append(head);
 const body=node('tbody');body.id='model-fields';
 for(const [field,column] of Object.entries(item.models[picker.value])){
  const constraints=[column.primaryKey?'primary key':'',column.generated?'generated':'',column.required||column.primaryKey?'required':'nullable',column.unique?'unique':''].filter(Boolean).join(' · ');
  const row=node('tr');row.append(node('td',field,'mono'),node('td',column.type+(column.maxLength?' ('+column.maxLength+')':'')),node('td',constraints));body.append(row);
 }
 table.append(body);el('models').append(table);
}
function renderDetail(){
 const item=snapshot.components[selectedId],source=snapshot.sources?.[selectedId];
 el('detail-empty').hidden=Boolean(selectedId);el('detail').hidden=!selectedId;if(!selectedId)return;
 syncPicker();text('detail-name',selectedId);badge(el('detail-state'),...state(selectedId));
 const error=snapshot.errors[selectedId];el('detail-error').hidden=!error;
 text('detail-error',error?(item?'Last working version is still serving. ':'Component could not load. ')+error:'');
 const facts=el('detail-facts');facts.replaceChildren();
 fact(facts,'Release',item?.release);fact(facts,'Authentication',item?.authMode==='public'?'Public · application may enforce its own access':item?.authMode);
 fact(facts,'Repository',source?.repository||'Local / remote specification');fact(facts,'Tracked ref',source?.ref||'—');
 fact(facts,'Active Git revision',item?.revision,true);fact(facts,'Loaded in this process',date(item?.loadedAt));
 fact(facts,'Database',item?.databaseBackend?item.databaseBackend+' · schema '+item.schemaVersion:'None');
 fact(facts,'Event transport',item?.realtime?item.realtime.backend+' · '+item.realtime.state+' · best effort'+(item.realtime.backend==='redis'?' · '+item.realtime.droppedRemoteEvents+' dropped remote events':''):'Memory · best effort');
 fact(facts,'Live traffic',item?item.activeRequests+' HTTP / auth in flight · '+item.websocketConnections+' sockets':'Not serving');
 text('detail-endpoint',item?location.origin+item.prefix:'No active component URL');el('copy-endpoint').disabled=!item;
 el('routes').replaceChildren();el('socket-note').hidden=!item?.websocket;
 if(!item){el('routes').append(node('p','No active routes.','empty'));renderModels(null);return;}
 for(const route of item.routes||[]){const row=node('div',undefined,'route');
  row.append(node('span',route.method,'method '+(route.method==='WS'?'socket':route.method==='GET'?'':'write')),node('code',route.path),
   node('span',route.kind+(route.scopes?.length?' · '+route.scopes.join(', '):''),'route-meta'));el('routes').append(row);}
 if(item.websocket)text('socket-note','WebSocket resources: '+item.websocket.resources.join(', ')+'. Paths below are relative to the component URL.');
 renderModels(item);
}
function syncKeys(){
 const previous=el('component').value,available=Object.entries(snapshot.components).filter(([,item])=>item.authMode==='database');
 el('component').replaceChildren();for(const [id] of available){const option=node('option',id);option.value=id;el('component').append(option);}
 if(available.some(([id])=>id===previous))el('component').value=previous;
 el('key-controls').hidden=!available.length;el('key-empty').hidden=Boolean(available.length);
 if(previous!==el('component').value)keySelection();else showScopes();
}
function render(result){
 snapshot=result;rememberCredential();
 el('login-card').hidden=true;el('controls').hidden=false;el('actions').hidden=false;el('logout').hidden=false;el('token').hidden=true;el('connect').hidden=true;
 badge(el('connection'),'Connected','good');diagnostics(result);
 const values=Object.values(result.components),runtime=result.runtime||{};
 text('metric-components',values.filter(item=>!item.blocked).length);text('metric-configured',Object.keys(result.sources||{}).length+' registered · '+(result.blocked||[]).length+' blocked');
 text('metric-errors',Object.keys(result.errors).length+(result.sourcesError?1:0));
 text('metric-sockets',values.reduce((sum,item)=>sum+(item.websocketConnections||0),0));
 text('metric-requests',values.reduce((sum,item)=>sum+(item.activeRequests||0),0)+' requests in flight');
 text('metric-check',runtime.checking?'Checking…':runtime.lastFinishedAt?new Date(runtime.lastFinishedAt).toLocaleTimeString():'Not yet');
 text('metric-poll','Specifications checked every '+runtime.pollSeconds+'s');
 el('source-error').hidden=!result.sourcesError;text('source-error','Sources check failed; previous configuration retained. '+(result.sourcesError||''));
 if(!ids().includes(selectedId))selectedId=ids()[0]||'';
 renderList();renderDetail();syncKeys();
 const facts=el('runtime-facts');facts.replaceChildren();
 badge(el('reload-state'),runtime.checking?'Checking':result.sourcesError?'Source error':Object.keys(result.errors).length?'Component errors':'Watching',result.sourcesError||Object.keys(result.errors).length?'warn':'good');
 fact(facts,'Process uptime',duration(runtime.uptimeSeconds||0));fact(facts,'Started',date(runtime.startedAt));
 fact(facts,'Last attempt',date(runtime.lastAttemptAt));fact(facts,'Last completed sources reconciliation',date(runtime.lastSuccessAt));
 fact(facts,'Old generations draining',runtime.retiringGenerations??0);fact(facts,'WebSocket drain window',runtime.drainSeconds+'s');
 fact(facts,'Trusted Python handlers',runtime.pythonEnabled?'Enabled':'Disabled');fact(facts,'Allowed browser origins',runtime.allowedOrigins?.join(', ')||'None configured');
 text('updated','Updated '+new Date().toLocaleTimeString());
}
async function status(){render(await api('status'));}
function selected(){return encodeURIComponent(el('component').value);}
function showScopes(){text('available','Declared scopes: '+(snapshot?.components[el('component').value]?.scopes||[]).join(', '));}
function keySelection(){showScopes();el('keys').replaceChildren(node('li','Refresh keys to load this component’s issued keys.','muted small'));clearKey();showKeyTab('list');}
async function list(){
 if(!selected())throw Error('Select a component with database authentication.');
 const component=selected(),result=await api('components/'+component+'/keys');
 if(component!==selected())return;el('keys').replaceChildren();
 if(!result.keys.length)el('keys').append(node('li','No keys issued for this component.','muted small'));
 for(const key of result.keys){const li=node('li'),state=key.revokedAt?'Revoked':key.expiresAt&&key.expiresAt*1000<=Date.now()?'Expired':'Active';
  li.append(node('strong',key.label),node('p',state+' · '+key.scopes.join(', '),'small muted'),node('p',key.id,'mono'),
   node('p','Created '+date(key.createdAt*1000)+' · Expires '+(key.expiresAt?date(key.expiresAt*1000):'never'),'small muted'));
  if(!key.revokedAt){const button=node('button','Revoke','cds--btn cds--btn--danger--tertiary cds--layout--size-sm');button.onclick=()=>{
   if(button.dataset.confirm!=='yes'){button.dataset.confirm='yes';button.textContent='Confirm revoke · clients will lose access';return;}
   run(async()=>{await api('components/'+component+'/keys/'+encodeURIComponent(key.id),'DELETE');clearKey();if(component===selected())await list();});};li.append(button);}
  el('keys').append(li);
 }

}
el('login').onsubmit=event=>{event.preventDefault();if(busy)return;session++;token=el('token').value.trim();el('token').value='';run(status);};
el('logout').onclick=()=>{disconnect();text('message','Disconnected.');};
el('refresh').onclick=()=>run(status);el('reconcile').onclick=()=>run(async()=>render(await api('reconcile','POST')));
el('search').oninput=()=>snapshot&&renderList();el('filter').onchange=()=>snapshot&&renderList();
el('copy-endpoint').onclick=()=>run(async()=>{await navigator.clipboard.writeText(el('detail-endpoint').textContent);text('updated','Component URL copied.');});
el('component').onchange=keySelection;el('list').onclick=()=>run(list);el('clear-key').onclick=clearKey;
el('create').onsubmit=event=>{event.preventDefault();run(async()=>{
 if(!selected())throw Error('Select a component.');clearKey();const component=selected();
 const key=await api('components/'+component+'/keys','POST',{label:el('label').value,scopes:el('scopes').value.split(',').map(s=>s.trim()).filter(Boolean),expiresInSeconds:Number(el('expiry').value)});
 if(component!==selected())throw Error('Component changed while creating the key. Revoke the unused key from its component.');
 el('new-key').value=key.apiKey;el('issued').hidden=false;showKeyTab('issued');await list();
 });};
setInterval(()=>{if(token&&el('auto').checked&&!document.hidden)run(async()=>{await status();if(currentView==='files')await listFiles();},true);},10000);
window.addEventListener('pagehide',disconnect);

initNavigation();
initFiles();
initCredentials();
