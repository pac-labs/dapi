import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

class Element {
 constructor(tag='div',value=''){this.tag=tag;this.textContent=value;this.children=[];this._value='';this.dataset={};this.style={};}
 get value(){return this._value;}
 set value(value){this._value=String(value);}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 scrollIntoView(){}
 set innerHTML(_){throw Error('Operator data must never be interpreted as HTML');}
}
const forms=readFileSync(new URL('../../looprd/panel_assets/operator_forms.js',import.meta.url),'utf8');
const data=readFileSync(new URL('../../looprd/panel_assets/operator_data.js',import.meta.url),'utf8');
function context(){
 const elements={},scope={node:(tag,value)=>new Element(tag,value),el:id=>elements[id]??=new Element(),run:fn=>fn(),crypto:{randomUUID:()=> 'intent-'+(++scope.generated)},generated:0};
 vm.createContext(scope);return {scope,elements};
}

test('operator controls preserve typed values, nulls and literal untrusted text',()=>{
 const {scope}=context();const {operatorControl}=vm.runInContext(forms+';({operatorControl})',scope);
 const number=operatorControl('amount',{type:'integer',required:true,label:'Amount'},5,'input');
 assert.equal(number.read(),5);number.wrapper.children[1].value='NaN';assert.throws(()=>number.read(),/valid number/);
 const optional=operatorControl('note',{type:'string',required:false,label:'Note'},null,'input');assert.equal(optional.read(),null);
 const literal=operatorControl('label',{type:'string',required:true,label:'<script>bad()</script>'},'<img src=x>','input');
 assert.equal(literal.wrapper.children[0].textContent,'<script>bad()</script>');assert.equal(literal.read(),'<img src=x>');
 const choice=operatorControl('role',{type:'string',required:true,choices:['player','admin']},'admin','input');assert.equal(choice.read(),'admin');
});

test('reviewing the same operator intent reuses its receipt key until inputs change',()=>{
 const {scope,elements}=context();scope.operatorButton=(label,action)=>Object.assign(new Element('button',label),{action});
 const api=vm.runInContext(forms+data+`;({reviewOperatorWrite,invalidateOperatorReview,
  setup(){operatorComponent='demo';operatorResource='accounts';operatorCatalog={generation:'g',resources:{accounts:{fields:{role:{label:'Role'}}}},actions:{}};operatorRecord={id:1,etag:'e',values:{role:'player'}};},
  identity(){return operatorIntent.operationId;}})`,scope);
 api.setup();elements['operator-reason']=Object.assign(new Element(),{value:'Support request'});
 api.reviewOperatorWrite({role:'admin'},null);const first=api.identity();api.reviewOperatorWrite({role:'admin'},null);assert.equal(api.identity(),first);
 api.invalidateOperatorReview();api.reviewOperatorWrite({role:'admin'},null);assert.notEqual(api.identity(),first);
});

test('busy actions restore existing disabled controls without enabling forbidden actions',async()=>{
 const panel=readFileSync(new URL('../../looprd/panel_assets/panel.js',import.meta.url),'utf8');
 const source=panel.slice(panel.indexOf('async function run('),panel.indexOf('function ids()'));
 const enabled={disabled:false,isConnected:true},disabled={disabled:true,isConnected:true};
 const scope={busy:false,storageUnavailable:false,document:{querySelectorAll:()=>[enabled,disabled]},text:()=>{},el:()=>({})};vm.createContext(scope);
 const run=vm.runInContext(source+';run',scope);
 await run(async()=>{assert.equal(enabled.disabled,true);assert.equal(disabled.disabled,true);});
 assert.equal(enabled.disabled,false);assert.equal(disabled.disabled,true);
});
