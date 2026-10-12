// Optional same-origin browser persistence, requested by the operator.
const credentialStorageKey='looprd.managementKey.v1';
let rememberedKey='',storageUnavailable=false;
function forgetCredential(){
 rememberedKey='';
 try{localStorage.removeItem(credentialStorageKey);el('forget-key').hidden=true;return true;}
 catch{storageUnavailable=true;el('forget-key').hidden=false;return false;}
}
function rememberCredential(){
 if(!token)return;
 if(!el('remember').checked){forgetCredential();return;}
 if(rememberedKey===token)return;
 try{localStorage.setItem(credentialStorageKey,token);rememberedKey=token;storageUnavailable=false;el('forget-key').hidden=false;}
 catch{storageUnavailable=true;}
}
function initCredentials(){
 try{rememberedKey=localStorage.getItem(credentialStorageKey)||'';}catch{storageUnavailable=true;}
 el('forget-key').hidden=!rememberedKey;
 el('forget-key').onclick=()=>{const removed=forgetCredential();el('remember').checked=false;disconnect();
  text('message',removed?'Saved management key forgotten.':'Disconnected. Browser storage could not be cleared; clear site data to remove the saved key.');};
 if(rememberedKey){token=rememberedKey;el('remember').checked=true;run(status);}
}
