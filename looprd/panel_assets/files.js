// Management-created invitations; capability URLs stay out of browser storage.
let fileInvitationId='';
function shellQuote(value){return "'"+value.replaceAll("'", "'\\''")+"'";}
function absoluteFileUrl(path){return new URL(path,location.origin).href;}
function clearFiles(){
 fileInvitationId='';
 el('file-command').value='';el('file-invitation').hidden=true;el('file-list').replaceChildren();
}
async function listFiles(){
 const result=await api('files');
 if(fileInvitationId&&!result.files.some(file=>file.id===fileInvitationId&&file.state==='pending')){
  fileInvitationId='';el('file-command').value='';el('file-invitation').hidden=true;
 }
 text('file-limits','One file per invitation · '+Math.floor(result.maxFileBytes/1048576)+' MiB maximum · expires within 12 hours · '+Math.floor(result.quotaBytes/1048576)+' MiB total quota');
 const list=el('file-list');list.replaceChildren();
 if(!result.files.length)list.append(node('p','No upload invitations. Request one above.','empty'));
 for(const file of result.files){
  const row=node('article',undefined,'file-row');
  row.append(node('strong',file.filename),node('p',file.state+' · '+file.size.toLocaleString()+' bytes · Expires '+date(file.expiresAt),'muted small'));
  if(file.downloadUrl){
   const url=absoluteFileUrl(file.downloadUrl),link=node('a','Download file','file-link');
   link.href=url;link.rel='noreferrer';row.append(link);
   const input=node('input');input.className='cds--text-input';input.readOnly=true;input.value=url;input.setAttribute('aria-label','Download URL for '+file.filename);row.append(input);
   const copy=node('button','Copy download URL','cds--btn cds--btn--tertiary cds--layout--size-sm');
   copy.onclick=()=>run(async()=>{await navigator.clipboard.writeText(url);text('updated','Download URL copied.');});row.append(copy);
  }else row.append(node('p',file.state==='pending'?'Waiting for the terminal upload. The download link appears here after completion.':file.state==='uploading'?'Upload in progress…':'Upload failed or interrupted. Delete this invitation and request another.','small'));
  const remove=node('button','Delete','cds--btn cds--btn--danger--tertiary cds--layout--size-sm');remove.disabled=file.state==='uploading';
  remove.onclick=()=>{
   if(remove.dataset.confirm!=='yes'){remove.dataset.confirm='yes';remove.textContent='Confirm delete';return;}
   run(async()=>{await api('files/'+encodeURIComponent(file.id),'DELETE');await listFiles();});
  };row.append(remove);list.append(row);
 }
}
function initFiles(){
 el('request-file').onsubmit=event=>{event.preventDefault();run(async()=>{
  const filename=el('file-name').value,result=await api('files','POST',{filename});
  const url=absoluteFileUrl(result.uploadUrl);
  fileInvitationId=result.id;
  el('file-command').value='curl --fail-with-body --upload-file '+shellQuote('./'+filename)+' '+shellQuote(url);
  el('file-invitation').hidden=false;text('file-expiry','Single-use upload invitation. Expires '+date(result.expiresAt)+'. Replace the local file path if needed.');
  await listFiles();
 });};
 el('refresh-files').onclick=()=>run(listFiles);
 el('copy-file-command').onclick=()=>run(async()=>{await navigator.clipboard.writeText(el('file-command').value);text('updated','Upload command copied.');});
}
