let operatorComponent='',operatorPreferred='',operatorCatalog=null,operatorResource='',operatorRecord=null;
let operatorOffset=0,operatorNext=null,operatorIntent=null,operatorReviewed=null,operatorDraft=false,operatorAuditOffset=0,operatorAuditNext=null;
function operatorPath(){return 'data/'+encodeURIComponent(operatorComponent);}
function operatorRowsPath(){return operatorPath()+'/resources/'+encodeURIComponent(operatorResource);}
function clearOperatorData(){
 operatorComponent='';operatorPreferred='';operatorCatalog=null;operatorResource='';operatorRecord=null;operatorIntent=null;operatorReviewed=null;operatorDraft=false;
 for(const id of ['operator-rows','operator-editor','operator-audit','operator-receipt'])el(id).replaceChildren();
 el('operator-component').replaceChildren();el('operator-resource').replaceChildren();el('operator-search').value='';
 el('operator-browser').hidden=false;el('operator-editor').hidden=true;el('operator-audit').hidden=true;
 el('operator-component').disabled=false;el('operator-resource').disabled=false;
}
function openOperatorComponent(id){
 if(operatorRecord&&operatorComponent!==id){showView('data');text('message','Close the current record before switching services.');return;}
 operatorPreferred=id;showView('data');
}
async function loadOperatorData(){
 if(operatorRecord)return;
 const result=await api('data'),picker=el('operator-component'),previous=operatorPreferred||operatorComponent;operatorPreferred='';picker.replaceChildren();
 for(const [id,item] of Object.entries(result.components)){
  const option=node('option',id+(item.enabled?'':' · administration disabled'));option.value=id;picker.append(option);
 }
 if([...picker.options].some(option=>option.value===previous))picker.value=previous;
 operatorComponent=picker.value;
 if(!operatorComponent){text('operator-guidance','No active services. Register a component to begin.');el('operator-workspace').hidden=true;return;}
 if(!result.components[operatorComponent].enabled){operatorCatalog=null;text('operator-guidance','This service has not enabled data administration. Its author must declare management resources, fields and actions in looprd.yaml. No records are exposed.');el('operator-workspace').hidden=true;return;}
 operatorCatalog=await api(operatorPath());el('operator-workspace').hidden=false;
 text('operator-guidance',result.components[operatorComponent].blocked?'Public API is blocked. Declared operator repairs remain available.':'Only fields and actions allowed by this service specification are available.');
 const resources=el('operator-resource'),previousResource=operatorResource;resources.replaceChildren();
 for(const [id,item] of Object.entries(operatorCatalog.resources)){const option=node('option',item.label);option.value=id;resources.append(option);}
 if([...resources.options].some(option=>option.value===previousResource))resources.value=previousResource;
 operatorResource=resources.value;operatorOffset=0;operatorAuditOffset=0;await loadOperatorRows();
}
async function loadOperatorRows(){
 if(!operatorCatalog||operatorRecord)return;
 el('operator-browser').hidden=false;el('operator-audit').hidden=true;
 const result=await api(operatorRowsPath()+'?offset='+operatorOffset+'&q='+encodeURIComponent(el('operator-search').value));operatorNext=result.nextOffset;
 const policy=operatorCatalog.resources[operatorResource],target=el('operator-rows');target.replaceChildren();
 if(!result.records.length){target.append(node('p','No matching records.','empty'));}
 else {
  const table=node('table',undefined,'cds--data-table'),head=node('thead'),headRow=node('tr');
  headRow.append(node('th','ID'),node('th','Actions'));for(const [field,schema] of Object.entries(policy.fields))headRow.append(node('th',schema.label||field));head.append(headRow);table.append(head);table.style.minWidth=Math.max(640,Object.keys(policy.fields).length*150)+'px';
  const body=node('tbody');for(const record of result.records){const row=node('tr');row.append(node('td',record.id));
   const actions=node('td');actions.append(operatorButton('Open',()=>openOperatorRecord(record.id)));row.append(actions);
   for(const field of Object.keys(policy.fields)){const value=record.values[field];row.append(node('td',value===null?'Not set':typeof value==='object'?JSON.stringify(value):String(value)));}body.append(row);}
  table.append(body);target.append(table);
 }
 text('operator-page',result.records.length?'Records '+(operatorOffset+1)+'–'+(operatorOffset+result.records.length):'No records');
 el('operator-prev').disabled=operatorOffset===0;el('operator-next').disabled=operatorNext===null;
}
function invalidateOperatorReview(){
 operatorDraft=true;operatorReviewed=null;operatorIntent=null;el('operator-confirmation').hidden=true;
}
async function openOperatorRecord(identity){
 const record=await api(operatorRowsPath()+'/'+identity);operatorRecord=record;operatorIntent=null;operatorReviewed=null;operatorDraft=false;
 const target=el('operator-editor');target.replaceChildren();target.hidden=false;el('operator-browser').hidden=true;el('operator-audit').hidden=true;
 el('operator-component').disabled=true;el('operator-resource').disabled=true;
 const policy=operatorCatalog.resources[operatorResource],actions=Object.entries(operatorCatalog.actions).filter(([,item])=>item.resource===operatorResource);
 const heading=node('div',undefined,'section-heading');heading.append(node('h3',(policy.titleField?record.values[policy.titleField]+' · ':'')+'Record #'+record.id));
 const close=operatorButton('Close record',async()=>{
  if(operatorDraft&&close.dataset.confirm!=='yes'){close.dataset.confirm='yes';close.textContent='Confirm discard draft';return;}
  operatorRecord=null;operatorDraft=false;operatorIntent=null;target.replaceChildren();target.hidden=true;el('operator-component').disabled=false;el('operator-resource').disabled=false;await loadOperatorRows();
 },'ghost');heading.append(close);target.append(heading);
 const fields=node('div',undefined,'operator-fields'),controls={};
 for(const [name,schema] of Object.entries(policy.fields)){
  if(schema.editable){const control=operatorControl(name,schema,record.values[name],'operator-edit');controls[name]=control;fields.append(control.wrapper);}
  else {const field=node('div',undefined,'operator-field');field.append(node('label',(schema.label||name)+' · read only'),node('p',typeof record.values[name]==='object'?JSON.stringify(record.values[name]):String(record.values[name]??'Not set'),'operator-readonly'));fields.append(field);}
 }
 fields.oninput=invalidateOperatorReview;target.append(node('h3','Record fields'),fields);
 const reasonLabel=node('label','Reason for this change');reasonLabel.htmlFor='operator-reason';const reason=node('input',undefined,'cds--text-input');reason.id='operator-reason';reason.maxLength=500;reason.placeholder='Support ticket or explanation';reason.oninput=invalidateOperatorReview;target.append(reasonLabel,reason);
 if(Object.keys(controls).length)target.append(operatorButton('Review field changes',async()=>{
  const values={};for(const [name,control] of Object.entries(controls)){const value=control.read();if(JSON.stringify(value)!==JSON.stringify(record.values[name]))values[name]=value;}
  if(!Object.keys(values).length)throw Error('Change at least one editable field.');reviewOperatorWrite(values,null);
 },'primary'));
 if(actions.length){
  target.append(node('h3','Management actions'));const selector=node('select',undefined,'cds--select-input');selector.id='operator-action';const actionLabel=node('label','Action');actionLabel.htmlFor=selector.id;target.append(actionLabel);
  const empty=node('option','Choose an action');empty.value='';selector.append(empty);for(const [name,action] of actions){const option=node('option',action.label);option.value=name;selector.append(option);}target.append(selector);
  const form=node('div',undefined,'operator-fields');target.append(form);selector.onchange=()=>{
   invalidateOperatorReview();form.replaceChildren();if(!selector.value)return;
   const action=operatorCatalog.actions[selector.value],inputs={};form.append(node('p',action.description||'This action runs inside an audited database transaction.','muted small'));
   for(const [name,schema] of Object.entries(action.inputs)){const control=operatorControl(name,schema,undefined,'operator-input');inputs[name]=control;form.append(control.wrapper);}
   form.oninput=invalidateOperatorReview;
   form.append(operatorButton('Review '+action.label,async()=>{const values={};for(const [name,control] of Object.entries(inputs))values[name]=control.read();reviewOperatorWrite(values,selector.value);},'primary'));
  };
 }
 const confirmation=node('div',undefined,'notice operator-confirmation');confirmation.id='operator-confirmation';confirmation.hidden=true;target.append(confirmation);
}
function reviewOperatorWrite(values,action){
 const reason=el('operator-reason').value.trim();if(reason.length<3)throw Error('Provide a reason for the change.');
 const same=operatorIntent&&operatorReviewed?.action===action&&operatorIntent.reason===reason&&JSON.stringify(operatorIntent.values)===JSON.stringify(values);
 const payload={generation:operatorCatalog.generation,etag:operatorRecord.etag,operationId:same?operatorIntent.operationId:crypto.randomUUID(),reason,values};
 operatorIntent=payload;operatorReviewed={action,payload};operatorDraft=true;
 const confirmation=el('operator-confirmation');confirmation.replaceChildren();confirmation.hidden=false;
 confirmation.append(node('h3','Confirm operator change'),node('p',operatorComponent+' / '+operatorResource+' / #'+operatorRecord.id),
  node('p',action?operatorCatalog.actions[action].label:'Edit record fields'),node('p','Reason: '+reason));
 const policies=action?operatorCatalog.actions[action].inputs:operatorCatalog.resources[operatorResource].fields;
 const changes=node('div',undefined,'operator-fields');
 for(const [name,value] of Object.entries(values)){const change=node('div',undefined,'operator-field');change.append(node('strong',policies[name].label||name));
  if(!action)change.append(node('p','Current: '+operatorValue(operatorRecord.values[name]),'small muted'));
  change.append(node('p',(action?'Value: ':'New: ')+operatorValue(value),'diagnostic-line'));changes.append(change);}
 confirmation.append(changes);
 if(action){const labels=[];for(const [resource,fields] of Object.entries(operatorCatalog.actions[action].writes))labels.push(operatorCatalog.resources[resource].label+': '+fields.map(name=>operatorCatalog.resources[resource].fields[name].label||name).join(', '));confirmation.append(node('p','This action may update '+labels.join('; ')+'.','small muted'));}
 confirmation.append(operatorButton('Confirm and apply',async()=>{
  if(!operatorReviewed)throw Error('Review the changes again before applying.');
  const selection=operatorReviewed,id=operatorRecord.id;
  const path=operatorRowsPath()+'/'+id+(selection.action?'/actions/'+encodeURIComponent(selection.action):'');
  const receipt=await api(path,selection.action?'POST':'PATCH',selection.payload);
  await openOperatorRecord(id);text('operator-receipt',(receipt.duplicate?'Previously applied':'Applied')+' · '+receipt.changedRecords+' record(s) · receipt '+receipt.operationId+(receipt.notificationWarning?' · Change saved; clients may need to refresh.':''));
 },'primary'));
 confirmation.scrollIntoView({block:'nearest'});
}
function operatorValue(value){return value===null?'Not set':typeof value==='boolean'?(value?'Yes':'No'):typeof value==='object'?JSON.stringify(value):String(value);}
async function loadOperatorAudit(){
 if(!operatorCatalog)return;
 const result=await api(operatorPath()+'/audit?offset='+operatorAuditOffset);operatorAuditNext=result.nextOffset;
 const target=el('operator-audit');target.replaceChildren();target.hidden=false;el('operator-browser').hidden=true;el('operator-editor').hidden=true;
 const actions=node('div',undefined,'toolbar');
 const previous=operatorButton('Previous entries',async()=>{operatorAuditOffset=Math.max(0,operatorAuditOffset-50);await loadOperatorAudit();});previous.disabled=operatorAuditOffset===0;
 const next=operatorButton('Next entries',async()=>{if(operatorAuditNext!==null){operatorAuditOffset=operatorAuditNext;await loadOperatorAudit();}});next.disabled=operatorAuditNext===null;
 actions.append(operatorButton('Back to records',async()=>{if(operatorRecord){target.hidden=true;el('operator-editor').hidden=false;}else await loadOperatorRows();},'ghost'),previous,next);target.append(actions);
 if(!result.entries.length)target.append(node('p','No operator changes recorded.','empty'));
 for(const entry of result.entries){const row=node('article',undefined,'file-row');row.append(node('strong',entry.operation+' · '+entry.target),
  node('p',entry.reason),node('p',date(entry.at)+' · Operator (shared management key)','muted small'),node('code',entry.id));
  for(const change of entry.changes)row.append(node('p',change.resource+' #'+change.id+': '+change.fields.map(name=>operatorCatalog.resources[change.resource]?.fields[name]?.label||name).join(', '),'small muted'));target.append(row);}
}
function initOperatorData(){
 el('operator-component').onchange=()=>{operatorPreferred=el('operator-component').value;operatorOffset=0;run(loadOperatorData);};
 el('operator-resource').onchange=()=>{operatorResource=el('operator-resource').value;operatorOffset=0;run(loadOperatorRows);};
 el('operator-find').onclick=()=>run(async()=>{operatorOffset=0;await loadOperatorRows();});
 el('operator-search').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();el('operator-find').click();}};
 el('operator-prev').onclick=()=>run(async()=>{operatorOffset=Math.max(0,operatorOffset-25);await loadOperatorRows();});
 el('operator-next').onclick=()=>run(async()=>{if(operatorNext!==null){operatorOffset=operatorNext;await loadOperatorRows();}});
 el('operator-reload').onclick=()=>run(async()=>{if(operatorRecord)throw Error('Close the current record before refreshing administration options.');await loadOperatorData();});
 el('operator-history').onclick=()=>run(async()=>{operatorAuditOffset=0;await loadOperatorAudit();});
}
