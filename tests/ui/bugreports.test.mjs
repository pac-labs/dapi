import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../../looprd/panel_assets/bugreports.js',import.meta.url),'utf8');
class Element {
 constructor(tag='div',text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.dataset={};this.hidden=false;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 get firstChild(){return this.children[0];}
 get options(){return this.children;}
 set innerHTML(_){throw Error('Untrusted report HTML must never be parsed');}
}
function panel(){
 const rows=[{id:'issue-a',component:'demo',category:'network',errorCode:'FAILED',message:'<img src=x onerror=alert(1)>',
  state:'open',occurrences:3,firstSeenAt:1000,lastSeenAt:2000,appVersion:'1',platform:'Windows',stackTrace:'<script>bad()</script>',diagnostics:{value:'<svg onload=bad()>'}}];
 const elements={},calls=[];
 const el=id=>elements[id]??=new Element();el('bugreport-state').value='all';
 const context={el,node:(tag,value)=>new Element(tag,value??''),text:(id,value)=>el(id).textContent=value,
  date:value=>String(value),fact:(target,label,value)=>target.append(new Element('dt',label),new Element('dd',value)),
  run:fn=>fn(),api:async(path,method='GET',body)=>{
   calls.push({path,method,body});
   if(method==='DELETE'){rows.splice(0,1);return;}
   if(method==='PATCH')rows[0].state=body.state;
   return path==='bugreports'?{reports:rows.map(row=>({...row}))}:{...rows[0]};
  }};
 vm.createContext(context);
 const api=vm.runInContext(source+';({initBugReports,loadBugReports,reviewBugReport,clearBugReports,renderBugReports})',context);
 api.initBugReports();return {api,elements,calls};
}
function strings(element){return [String(element.textContent),...element.children.flatMap(strings)];}

test('bug report filtering and details render diagnostic content as literal text',async()=>{
 const {api,elements}=panel();await api.loadBugReports();
 assert.equal(elements['bugreport-list'].children.length,1);
 assert.ok(strings(elements['bugreport-list']).includes('<img src=x onerror=alert(1)>'));
 elements['bugreport-search'].value='unmatched';api.renderBugReports();
 assert.ok(strings(elements['bugreport-list']).includes('No matching bug reports.'));
 await api.reviewBugReport('issue-a');
 assert.equal(elements['bugreport-browser'].hidden,true);
 assert.ok(strings(elements['bugreport-detail']).includes('<script>bad()</script>'));
 assert.ok(strings(elements['bugreport-detail']).some(text=>text.includes('<svg onload=bad()>')));
 api.clearBugReports();assert.equal(elements['bugreport-detail'].children.length,0);
 assert.equal(elements['bugreport-search'].value,'');
});

test('review actions resolve reports and require confirmation before deletion',async()=>{
 const {api,elements,calls}=panel();await api.reviewBugReport('issue-a');
 await elements['bugreport-detail'].children[0].children[1].onclick();
 assert.ok(calls.some(call=>call.method==='PATCH'&&call.body.state==='resolved'));
 const remove=elements['bugreport-detail'].children[0].children[2];
 remove.onclick();assert.equal(calls.filter(call=>call.method==='DELETE').length,0);
 await remove.onclick();assert.equal(calls.filter(call=>call.method==='DELETE').length,1);
 assert.equal(elements['bugreport-detail'].hidden,true);
});
