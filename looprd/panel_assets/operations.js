// Metadata-only operations; panel content uses text nodes, never HTML interpolation.
async function loadOperations(){
 const result=await api('ops'),target=el('operations-content');target.replaceChildren();
 for(const [id,value] of Object.entries(result.components)){
  const card=node('article',undefined,'card');card.append(node('h3',id));
  card.append(node('p',value.httpInFlight+' requests in flight · '+value.webSockets+' sockets'));
  for(const [name,error] of Object.entries(value))if(name.endsWith('Error')&&error)card.append(node('p',name+': '+error,'error'));
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
