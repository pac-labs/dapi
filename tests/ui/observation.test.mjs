import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../../looprd/panel_assets/observation.js',import.meta.url),'utf8');
class Element{
 constructor(tag='div',value=''){this.tag=tag;this.textContent=value;this.children=[];this.value='';}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(){}
 set innerHTML(_){throw Error('Do not parse operator metadata as HTML');}
}
function panel(){
 const elements={},calls=[];const el=id=>elements[id]??=new Element();
 const rows=[{name:'<img src=x onerror=alert(1)>',component:'demo',kind:'periodic job',state:'waiting',nextAt:100,cadenceSeconds:60,apiBlocked:true}];
 const context={el,node:(tag,value)=>new Element(tag,value??''),text:(id,value)=>el(id).textContent=value,
  date:value=>'time:'+value,duration:value=>value+'s',metricBytes:value=>value===null||value===undefined?'Not available':value+' bytes',
  run:fn=>fn(),api:async path=>{calls.push(path);return {generatedAt:10,items:rows,truncated:true};}};
 vm.createContext(context);const api=vm.runInContext(source+';({initObservation,loadSchedule,renderSchedule,renderStorage,clearObservation})',context);
 api.initObservation();return {api,elements,calls,el};
}
const strings=element=>[String(element.textContent),...element.children.flatMap(strings)];

test('schedule renders literal metadata, SQL due dates, filtering and API block semantics',async()=>{
 const {api,elements}=panel();await api.loadSchedule();
 const text=strings(elements['schedule-content']).join(' ');
 assert.ok(text.includes('<img src=x onerror=alert(1)>'));
 assert.ok(text.includes('time:100000'));assert.ok(text.includes('API blocked; jobs continue'));
 assert.ok(elements['schedule-summary'].textContent.includes('some activities omitted'));
 elements['schedule-search'].value='no match';api.renderSchedule();
 assert.ok(strings(elements['schedule-content']).includes('No matching activities.'));
 api.clearObservation();assert.equal(elements['schedule-content'].children.length,0);
 assert.equal(elements['schedule-search'].value,'');
});

test('storage distinguishes incomplete measurement, host capacity and PostgreSQL/Redis unknown values',()=>{
 const {api,elements}=panel();
 api.renderStorage({measuredAt:10,ageSeconds:2,state:'partial',cacheSeconds:30,measuredBytes:123,allocatedBytes:null,
  filesystem:{usedPercent:95,totalBytes:10000,freeBytes:500,level:'critical'},
  categories:[{category:'<svg onload=bad()>',component:'demo',bytes:123,allocatedBytes:null,files:2}],
  host:{state:'stale',ageSeconds:90,memorySupported:false,storage:[{name:'looprd-postgres-data',allocatedBytes:100,state:'available'},
   {name:'looprd-redis-data',allocatedBytes:null,state:'unavailable'}],containers:[{name:'looprd-redis',memoryBytes:null,memoryLimitBytes:null}]}});
 const text=strings(elements['storage-content']).join(' ');
 for(const value of ['Critical','Partial measurement','stale','looprd-postgres-data','looprd-redis-data','Not available','<svg onload=bad()>','Redis memory usage cannot be measured'])assert.ok(text.includes(value),value);
 assert.ok(text.includes('not added together'));assert.ok(text.includes('backups'));
 api.clearObservation();assert.equal(elements['storage-content'].children.length,0);
});
