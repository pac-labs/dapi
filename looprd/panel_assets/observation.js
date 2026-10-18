// Read-only operator agenda and disk accounting. Untrusted labels stay literal text.
let scheduleReport=null;
function observationTable(target,headings,rows){
 const table=node('table',undefined,'cds--data-table cds--layout--size-sm'),head=node('thead'),tr=node('tr');
 for(const heading of headings){const th=node('th',heading);th.scope='col';tr.append(th);}head.append(tr);table.append(head);
 const body=node('tbody');for(const values of rows){const row=node('tr');for(const value of values)row.append(node('td',value));body.append(row);}
 table.append(body);target.append(table);
}
function clearObservation(){
 scheduleReport=null;
 for(const id of ['schedule-content','storage-content'])el(id).replaceChildren();
 text('schedule-summary','Connect to view scheduled activities.');text('storage-summary','Connect to measure storage.');
 el('schedule-search').value='';
}
async function loadSchedule(){scheduleReport=await api('schedule');renderSchedule();}
function renderSchedule(){
 if(!scheduleReport)return;
 const target=el('schedule-content');target.replaceChildren();
 const search=el('schedule-search').value.trim().toLowerCase();
 const items=scheduleReport.items.filter(row=>[row.name,row.component,row.kind,row.state].join(' ').toLowerCase().includes(search));
 text('schedule-summary',items.length+' activities · sampled '+date(scheduleReport.generatedAt*1000)+(scheduleReport.truncated?' · bounded results; some activities omitted':''));
 observationTable(target,['Activity / service','Type','State','Due / lease expiry','Frequency'],items.map(row=>[
  row.name+' / '+(row.component||'Looprd'),row.kind,
  row.state+(row.apiBlocked?' · API blocked; jobs continue':'')+(row.error?' · '+row.error:''),
  row.nextAt?(row.state==='running'&&row.kind!=='maintenance'?'Lease until ':'')+date(row.nextAt*1000):row.cadenceSeconds?'Cadence only':'Event-driven',
  row.cadenceSeconds?'Every '+duration(row.cadenceSeconds):'—'
 ]));
 if(!items.length)target.append(node('p','No matching activities.','empty'));
}
async function loadStorage(){renderStorage(await api('storage'));}
function renderStorage(report){
 const target=el('storage-content');target.replaceChildren();
 text('storage-summary','Measured '+date(report.measuredAt*1000)+' · '+report.ageSeconds+' seconds old · '+report.state+' scan');
 const fs=report.filesystem,card=node('article',undefined,'card');
 card.append(node('h3','Filesystem capacity'));
 card.append(node('p',fs.usedPercent+'% used · '+metricBytes(fs.freeBytes)+' free of '+metricBytes(fs.totalBytes),fs.level==='normal'?'':'error'));
 const meter=node('progress');meter.max=100;meter.value=fs.usedPercent;meter.setAttribute('aria-label','Filesystem used');card.append(meter);
 if(fs.level!=='normal')card.append(node('p',fs.level==='critical'?'Critical: disk space is low. Review storage before large uploads or updates.':'Warning: filesystem usage has reached 80%.','error'));
 card.append(node('p','This filesystem also holds data outside Looprd. Capacity figures are not a total of the categories below.','small muted'));target.append(card);
 target.append(node('h3','Looprd data volume'));
 target.append(node('p',metricBytes(report.measuredBytes)+' file content · '+metricBytes(report.allocatedBytes)+' allocated on disk','small'));
 if(report.state!=='complete')target.append(node('p','Partial measurement: limits, skipped links or unreadable/changing files. Category sizes may be lower bounds.','error'));
 observationTable(target,['Category / service','File content','Allocated on disk','Files'],report.categories.map(row=>[
  row.category+(row.component?' / '+row.component:''),metricBytes(row.bytes),metricBytes(row.allocatedBytes),row.files
 ]));
 target.append(node('h3','Companion volumes'));
 const host=report.host;
 if(!host?.storage){target.append(node('p','Not measured. Enable storage collection in the optional host metrics sampler.','muted small'));}
 else{
  target.append(node('p',host.state==='available'?'Host sample '+host.ageSeconds+' seconds old':'Host measurements are '+host.state+'; values may be stale.','small muted'));
  observationTable(target,['Volume','Allocated on disk','State'],host.storage.map(row=>[row.name,metricBytes(row.allocatedBytes),row.state]));
 }
 target.append(node('p','PostgreSQL and Redis are included when their named volumes are configured. A missing volume is unknown, not zero. Redis may be configured without persistence.','small muted'));
 target.append(node('h3','Service memory (separate from disk)'));
 if(host?.containers?.length){
  observationTable(target,['Container','Memory used','Memory limit'],host.containers.map(row=>[row.name,metricBytes(row.memoryBytes),metricBytes(row.memoryLimitBytes)]));
  if(host.memorySupported===false)target.append(node('p','Host memory accounting is unavailable. Redis memory usage cannot be measured through this sampler on this host.','small muted'));
 }else target.append(node('p','Host memory metrics are not available.','small muted'));
 target.append(node('p','Volume figures overlap the data breakdown and are not added together. SQLite sizes include journals where present; PostgreSQL volume sizes include its own overhead.','small muted'));
 target.append(node('p','Not measured here: container image layers, host logs, backups and other unmounted directories. No automatic cleanup or deletion is performed.','small muted'));
 target.append(node('p','Measurements are cached for up to '+report.cacheSeconds+' seconds. File-content sizes can differ from physical allocation.','small muted'));
}
function initObservation(){
 el('refresh-schedule').onclick=()=>run(loadSchedule);el('refresh-storage').onclick=()=>run(loadStorage);
 el('schedule-search').oninput=renderSchedule;
}
