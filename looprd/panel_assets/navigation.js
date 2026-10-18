// Fixed workspace navigation with contained panels; no document scrolling.
let currentView='overview',detailTab='summary',keyTab='list';
const viewNames={overview:'Service overview',components:'Service inspection',data:'Data & management actions',access:'Access keys',files:'Temporary files',operations:'Operations',schedule:'Scheduled activities',storage:'Storage usage',runtime:'Runtime & reloads',diagnostics:'Diagnostics & agent access',bugreports:'Application bug reports'};
function showView(view){
 currentView=view;
 document.querySelectorAll('[data-view]').forEach(n=>n.hidden=n.dataset.view!==view);
 document.querySelectorAll('[data-view-button]').forEach(n=>{
  if(n.dataset.viewButton===view)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');
 });
 text('page-title',viewNames[view]);
 el('workspace-view').value=view;
 if(view==='data'&&token)run(loadOperatorData);
 if(view==='overview'&&token)loadOperatorQueue();
 if(view==='files'&&token)run(listFiles);
 if(view==='operations'&&token)run(loadOperations);
 if(view==='schedule'&&token)run(loadSchedule);
 if(view==='storage'&&token)run(loadStorage);
 if(view==='diagnostics'&&token)run(loadDiagnostics);
 if(view==='bugreports'&&token)run(loadBugReports);
}
function showDetailTab(tab){
 detailTab=tab;
 document.querySelectorAll('[data-detail-tab]').forEach(n=>n.hidden=n.dataset.detailTab!==tab);
 document.querySelectorAll('[data-tab-button]').forEach(n=>{
  if(n.dataset.tabButton===tab)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');
 });
}
function showKeyTab(tab){
 keyTab=tab;
 document.querySelectorAll('[data-key-tab]').forEach(n=>n.hidden=n.dataset.keyTab!==tab);
 document.querySelectorAll('[data-key-button]').forEach(n=>{
  if(n.dataset.keyButton===tab)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');
 });
}
function diagnostics(result){
 el('status').replaceChildren(...JSON.stringify(result,null,2).split('\n').map(line=>node('span',line,'diagnostic-line')));
}
function syncPicker(){
 const picker=el('component-picker');picker.replaceChildren();
 for(const id of ids()){const option=node('option',id);option.value=id;picker.append(option);}picker.value=selectedId;
 const button=el('block-api');button.hidden=!selectedId;
 const blocked=snapshot.blocked?.includes(selectedId)||false;
 if(button.dataset.component!==selectedId||button.dataset.blocked!==String(blocked)){
  button.dataset.component=selectedId;button.dataset.blocked=String(blocked);delete button.dataset.confirm;
  button.textContent=blocked?'Unblock API':'Block API';
 }
}
function initNavigation(){
 el('workspace-view').onchange=()=>showView(el('workspace-view').value);
 document.querySelectorAll('[data-view-button]').forEach(n=>n.onclick=()=>showView(n.dataset.viewButton));
 document.querySelectorAll('[data-tab-button]').forEach(n=>n.onclick=()=>showDetailTab(n.dataset.tabButton));
 document.querySelectorAll('[data-key-button]').forEach(n=>n.onclick=()=>showKeyTab(n.dataset.keyButton));
 el('component-picker').onchange=()=>{selectedId=el('component-picker').value;renderList();renderDetail();syncPicker();};
 el('model-picker').onchange=()=>{renderModels(snapshot.components[selectedId]);};
 el('block-api').onclick=()=>{
  const button=el('block-api'),id=selectedId,blocked=!snapshot.blocked?.includes(id);
  if(button.dataset.confirm!=='yes'){button.dataset.confirm='yes';button.textContent=blocked?'Confirm block · disconnect clients':'Confirm unblock API';return;}
  run(async()=>{render(await api('components/'+encodeURIComponent(id)+'/availability','PUT',{blocked}));});
 };
 document.querySelector('.brand').onclick=event=>{event.preventDefault();showView('overview');};
 showView('overview');showDetailTab('summary');showKeyTab('list');
}
