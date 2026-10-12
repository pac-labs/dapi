import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source=readFileSync(new URL('../../looprd/panel_assets/credentials.js',import.meta.url),'utf8');
function panel(storage){
 const elements={'remember':{checked:true},'forget-key':{hidden:true}};
 const context={token:'',localStorage:storage,el:id=>elements[id],status:()=>{},
  run:()=>{context.connected=true;},disconnect:()=>{context.token='';context.disconnected=true;},
  text:(id,value)=>{context.message=value;}};
 vm.createContext(context);
 const api=vm.runInContext(source+';({initCredentials,rememberCredential,forgetCredential})',context);
 return {api,context,elements};
}
function storage(){
 const values=new Map();
 return {values,getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
}

test('remembered credentials reconnect, opt out removes them, and Forget clears access',()=>{
 const saved=storage(),first=panel(saved);first.api.initCredentials();
 first.context.token='test-only-key';first.api.rememberCredential();
 const next=panel(saved);next.api.initCredentials();
 assert.equal(next.context.token,'test-only-key');assert.equal(next.context.connected,true);
 next.elements.remember.checked=false;next.api.rememberCredential();
 assert.equal(saved.values.size,0);assert.equal(next.context.token,'test-only-key');
 next.elements.remember.checked=true;next.api.rememberCredential();
 next.elements['forget-key'].onclick();
 assert.equal(saved.values.size,0);assert.equal(next.context.disconnected,true);
 assert.equal(next.context.token,'');assert.equal(next.elements['forget-key'].hidden,true);
});

test('unavailable browser storage leaves the session usable and failed removal is explicit',()=>{
 const denied=()=>{throw Error('storage unavailable');};
 const current=panel({getItem:denied,setItem:denied,removeItem:denied});
 current.api.initCredentials();current.context.token='test-only-key';
 assert.doesNotThrow(()=>current.api.rememberCredential());
 assert.equal(current.context.token,'test-only-key');
 current.elements['forget-key'].onclick();
 assert.equal(current.context.disconnected,true);
 assert.match(current.context.message,/could not be cleared/);
 assert.equal(current.elements['forget-key'].hidden,false);
});
