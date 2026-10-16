// Metadata-only operations; panel content uses text nodes, never HTML interpolation.
async function loadOperations(){
 const result=await api('ops'),target=el('operations-content');target.replaceChildren();
 renderContainerMetrics(target,result.containers);
 for(const [id,value] of Object.entries(result.components)){
  const card=node('article',undefined,'card');card.append(node('h3',id));
  card.append(node('p',value.httpInFlight+' requests in flight · '+value.webSockets+' sockets'));
  for(const [name,error] of Object.entries(value))if(name.endsWith('Error')&&error)card.append(node('p',name+': '+error,'error'));
  if(value.pushEnabled){
   const push=node('button','Inspect push delivery','cds--btn cds--btn--tertiary cds--layout--size-sm');
   push.onclick=()=>run(async()=>{const state=await api('components/'+encodeURIComponent(id)+'/push');
    const summary=node('p',state.devices+' devices · '+state.queued+' queued hints · '+state.failed+' failed hints');
    if(!state.configured)summary.append(node('span',' · provider not configured','error'));
    if(state.error)summary.append(node('span',' · '+state.error,'error'));push.replaceWith(summary);});card.append(push);
  }
  const refresh=node('button','Inspect jobs','cds--btn cds--btn--tertiary cds--layout--size-sm');
  refresh.onclick=()=>run(async()=>{
   const jobs=await api('components/'+encodeURIComponent(id)+'/jobs');
   const list=node('div');
   for(const job of jobs.jobs){
    const row=node('p',job.name+' · '+job.state+' · attempts '+job.attempts);
    if(job.error)row.append(node('span',' · '+job.error));
    if(job.state==='failed'){
     const retry=node('button','Retry job','cds--btn cds--btn--tertiary cds--layout--size-sm');
     retry.onclick=()=>run(async()=>{await api('components/'+encodeURIComponent(id)+'/jobs/'+encodeURIComponent(job.id)+'/retry','POST');await loadOperations();});row.append(retry);
    }list.append(row);
   }refresh.remove();card.append(list);
  });card.append(refresh);target.append(card);
 }
 target.append(node('h3','Recent request totals'));
 const totals={};for(const row of result.metrics){const value=totals[row.component]??={requests:0,errors:0,milliseconds:0};for(const field of Object.keys(value))value[field]+=row[field];}
 for(const [id,value] of Object.entries(totals))target.append(node('p',id+' · '+value.requests+' requests · '+value.errors+' server errors · '+Math.round(value.milliseconds/value.requests)+' ms mean'));
 target.append(node('p','Showing up to 1,000 recent minute records. Telemetry dropped: '+result.dropped,'muted small'));
 target.append(node('h3','Recent management activity'));
 for(const row of result.audit)target.append(node('p',new Date(row.at*1000).toLocaleString()+' · '+row.component+' · '+row.action+' · HTTP '+row.status,'small'));
}

function metricBytes(value){
 if(value===null||value===undefined)return 'Not available';
 const units=['B','KiB','MiB','GiB','TiB'];let index=0;
 while(value>=1024&&index<units.length-1){value/=1024;index++;}
 return value.toFixed(index?1:0)+' '+units[index];
}
function renderContainerMetrics(target,snapshot){
 target.append(node('h3','Runtime containers'));
 if(!snapshot||snapshot.state==='disabled'){
  target.append(node('p','Host container metrics are not configured.','muted small'));return;
 }
 const fresh=snapshot.state==='available';
 target.append(node('p',fresh?'Sampled '+snapshot.ageSeconds+' seconds ago':snapshot.state==='stale'?'Metrics are stale · last sample '+snapshot.ageSeconds+' seconds ago':'Host metrics are unavailable',fresh?'muted small':'error'));
 if(snapshot.memorySupported===false)target.append(node('p','Container memory accounting is unavailable on this host.','muted small'));
 for(const item of snapshot.containers){
  const card=node('article',undefined,'card');card.append(node('h4',item.name));
  card.append(node('p',item.state+' · restarts '+(item.restarts??'Not available')+(fresh?'':' · last known values')));
  const cpu=item.cpuPercent===null||item.cpuPercent===undefined?'Not available':item.cpuPercent.toFixed(1)+'%';
  card.append(node('p','CPU '+cpu+' · memory '+metricBytes(item.memoryBytes)+' · processes '+(item.pids??'Not available')));
  card.append(node('p','Network received '+metricBytes(item.networkRxBytes)+' · sent '+metricBytes(item.networkTxBytes),'small'));
  card.append(node('p','Disk read '+metricBytes(item.blockReadBytes)+' · written '+metricBytes(item.blockWriteBytes),'small'));
  if(item.error)card.append(node('p','Some measurements are unavailable.','error'));
  target.append(card);
 }
 target.append(node('p','CPU: 100% represents one core. I/O totals cover the current container lifetime.','muted small'));
}
