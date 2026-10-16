let operatorQueueLoading=false;
function renderOperatorAttention(){
 const target=el('operator-attention');target.replaceChildren();let count=0;
 for(const id of ids()){
  const [label,tone]=state(id);if(tone!=='warn')continue;count++;
  const row=node('button',undefined,'attention-item');row.type='button';row.append(node('strong',id),node('span',label,'badge warn'),node('p',snapshot.errors[id]||'Public API is blocked.','small muted'));
  row.onclick=()=>{selectedId=id;showView('components');renderDetail();};target.append(row);
 }
 if(snapshot.sourcesError){count++;target.prepend(node('p','Sources check failed. Working services are retained; inspect Runtime for details.','notice warning'));}
 if(!count)target.append(node('p','No service issues need attention.','muted small'));
 text('operator-attention-count',count+' service alerts');
}
async function loadOperatorQueue(){
 if(operatorQueueLoading||!token)return;operatorQueueLoading=true;
 try {const result=await api('bugreports'),open=result.reports.filter(item=>item.state==='open');text('operator-open-reports',open.length+' open application issues');}
 catch {if(token)text('operator-open-reports','Report inbox unavailable');}
 finally {operatorQueueLoading=false;}
}
function initOperatorOverview(){
 el('operator-go-data').onclick=()=>showView('data');el('operator-go-reports').onclick=()=>showView('bugreports');
 el('operator-go-operations').onclick=()=>showView('operations');
 el('administer-component').onclick=()=>openOperatorComponent(selectedId);
}
