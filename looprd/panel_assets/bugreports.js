// Application report content is untrusted: render exclusively as text.
let reportRows=[],selectedReport='';
function clearBugReports(){
 reportRows=[];selectedReport='';
 el('bugreport-list').replaceChildren();el('bugreport-detail').replaceChildren();
 el('bugreport-search').value='';el('bugreport-state').value='all';
 el('bugreport-component').replaceChildren(node('option','All components'));el('bugreport-component').firstChild.value='';
 text('bugreport-count','');el('bugreport-detail').hidden=true;el('bugreport-browser').hidden=false;
}
async function loadBugReports(){
 if(selectedReport){await reviewBugReport(selectedReport);return;}
 const result=await api('bugreports');reportRows=result.reports;
 const picker=el('bugreport-component'),previous=picker.value;
 picker.replaceChildren(node('option','All components'));picker.firstChild.value='';
 for(const id of [...new Set(reportRows.map(item=>item.component))].sort()){
  const option=node('option',id);option.value=id;picker.append(option);
 }
 if([...picker.options].some(option=>option.value===previous))picker.value=previous;
 renderBugReports();
}
function renderBugReports(){
 const list=el('bugreport-list'),search=el('bugreport-search').value.toLowerCase();list.replaceChildren();
 const filtered=reportRows.filter(item=>(!el('bugreport-component').value||item.component===el('bugreport-component').value)
  &&(el('bugreport-state').value==='all'||item.state===el('bugreport-state').value)
  &&[item.component,item.message,item.errorCode,item.category].join(' ').toLowerCase().includes(search));
 text('bugreport-count',filtered.length+' shown · '+reportRows.length+' / 100 issues stored');
 if(!filtered.length)list.append(node('p','No matching bug reports.','empty'));
 for(const item of filtered){
  const row=node('article',undefined,'file-row');
  row.append(node('strong',item.component+' · '+item.category+(item.errorCode?' · '+item.errorCode:'')),
   node('p',item.message.slice(0,200)),node('p',item.state+' · '+item.occurrences+' occurrences · '+date(item.lastSeenAt),'small muted'));
  const review=node('button','Review','cds--btn cds--btn--tertiary cds--layout--size-sm');
  review.onclick=()=>run(()=>reviewBugReport(item.id));row.append(review);list.append(row);
 }
}
async function reviewBugReport(id){
 const item=await api('bugreports/'+encodeURIComponent(id));selectedReport=id;
 const detail=el('bugreport-detail');detail.replaceChildren();detail.hidden=false;el('bugreport-browser').hidden=true;
 const actions=node('div',undefined,'toolbar'),back=node('button','Back to issues','cds--btn cds--btn--ghost');
 back.onclick=()=>run(async()=>{selectedReport='';detail.hidden=true;el('bugreport-browser').hidden=false;await loadBugReports();});
 const state=node('button',item.state==='open'?'Resolve issue':'Reopen issue','cds--btn cds--btn--secondary');
 state.onclick=()=>run(async()=>{await api('bugreports/'+id,'PATCH',{state:item.state==='open'?'resolved':'open'});await reviewBugReport(id);});
 const remove=node('button','Delete issue','cds--btn cds--btn--danger--tertiary');
 remove.onclick=()=>{
  if(remove.dataset.confirm!=='yes'){remove.dataset.confirm='yes';remove.textContent='Confirm permanent deletion';return;}
  return run(async()=>{await api('bugreports/'+id,'DELETE');selectedReport='';detail.replaceChildren();detail.hidden=true;el('bugreport-browser').hidden=false;await loadBugReports();});
 };
 actions.append(back,state,remove);detail.append(actions,node('h3',item.component+' · '+item.category));
 const facts=node('div',undefined,'facts');
 for(const [label,value] of [['State',item.state],['Occurrences',item.occurrences],['First seen',date(item.firstSeenAt)],
  ['Last seen',date(item.lastSeenAt)],['Latest application version',item.appVersion],['Latest platform',item.platform||'Unspecified'],['Error code',item.errorCode||'Unspecified']])fact(facts,label,value);
 detail.append(facts,node('h3','Latest diagnostic sample'),node('p',item.message,'diagnostic-line'),
  node('pre',item.stackTrace||'No stack trace supplied.','diagnostic-line'),node('pre',JSON.stringify(item.diagnostics,null,2),'diagnostic-line'));
}
function initBugReports(){
 el('refresh-bugreports').onclick=()=>run(loadBugReports);
 el('bugreport-search').oninput=renderBugReports;el('bugreport-state').onchange=renderBugReports;
 el('bugreport-component').onchange=renderBugReports;
}
