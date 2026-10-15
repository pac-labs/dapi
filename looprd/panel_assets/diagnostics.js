// Diagnostic capabilities reveal sanitized metadata only and expire automatically.
async function loadDiagnostics(){
 const result=await api('diagnostics'),list=el('diagnostic-failures');list.replaceChildren();
 if(!result.snapshot.failures.length)list.append(node('p','No failures recorded in the last seven days.','muted'));
 for(const failure of result.snapshot.failures){
  const card=node('article',undefined,'card');
  card.append(node('strong',(failure.component||'Runtime')+' · '+failure.code),node('p',failure.message),node('p',failure.action,'muted'),
   node('p',failure.stage+' · '+date(failure.lastSeenAt*1000),'small'),node('pre',JSON.stringify(failure.details,null,2),'mono'));list.append(card);
 }
 const links=el('diagnostic-links');links.replaceChildren();
 for(const link of result.links){const row=node('div',undefined,'card'),button=node('button','Revoke','cds--btn cds--btn--danger--tertiary cds--layout--size-sm');
  row.append(node('p',(link.component||'All components')+' · expires '+date(link.expiresAt*1000)),button);
  button.onclick=()=>run(async()=>{await api('diagnostics/'+encodeURIComponent(link.id),'DELETE');el('diagnostic-url').value='';await loadDiagnostics();});links.append(row);
 }
 const picker=el('diagnostic-component'),previous=picker.value;picker.replaceChildren(node('option','All components'));
 picker.firstChild.value='';for(const id of ids()){const option=node('option',id);option.value=id;picker.append(option);}picker.value=previous;
}
function initDiagnostics(){
 el('refresh-diagnostics').onclick=()=>run(loadDiagnostics);
 el('create-diagnostic').onclick=()=>run(async()=>{
  const result=await api('diagnostics','POST',{component:el('diagnostic-component').value||null,expiresInSeconds:Number(el('diagnostic-expiry').value)});
  el('diagnostic-url').value=location.origin+result.path;await loadDiagnostics();
 });
 el('copy-diagnostic').onclick=()=>run(async()=>{if(!el('diagnostic-url').value)throw Error('Create a diagnostic URL first.');await navigator.clipboard.writeText(el('diagnostic-url').value);});
}
