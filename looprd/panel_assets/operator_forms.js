// Shared typed controls; values and component metadata never become HTML.
function operatorControl(name,schema,value,prefix){
 const wrapper=node('div',undefined,'operator-field'),label=schema.label||name;
 const caption=node('label',label);caption.htmlFor=prefix+'-'+name;wrapper.append(caption);
 let input;
 if(schema.choices){input=node('select',undefined,'cds--select-input');for(const choice of schema.choices){const option=node('option',String(choice));option.value=JSON.stringify(choice);input.append(option);}input.value=JSON.stringify(value);}
 else if(schema.type==='boolean'){input=node('select',undefined,'cds--select-input');for(const choice of [false,true]){const option=node('option',choice?'Yes':'No');option.value=String(choice);input.append(option);}input.value=String(value??false);}
 else if(schema.type==='json'){input=node('textarea',undefined,'cds--text-area');input.rows=4;input.value=JSON.stringify(value??null,null,2);}
 else {input=node('input',undefined,'cds--text-input');input.type=['integer','float'].includes(schema.type)?'number':'text';input.value=value??'';
  if(input.type==='number'){input.step=schema.type==='integer'?'1':'any';if(schema.minimum!==null&&schema.minimum!==undefined)input.min=schema.minimum;if(schema.maximum!==null&&schema.maximum!==undefined)input.max=schema.maximum;}
  if(schema.maxLength)input.maxLength=schema.maxLength;}
 input.id=prefix+'-'+name;wrapper.append(input);
 const hints=[schema.required?'Required':'Optional'];if(schema.minimum!==null&&schema.minimum!==undefined)hints.push('Minimum '+schema.minimum);if(schema.maximum!==null&&schema.maximum!==undefined)hints.push('Maximum '+schema.maximum);if(schema.type==='string'&&schema.maxLength)hints.push('Up to '+schema.maxLength+' characters');wrapper.append(node('p',hints.join(' · '),'small muted'));
 let clear=null;
 if(!schema.required&&(!schema.choices||schema.choices.includes(null))){
  const clearLabel=node('label',undefined,'inline');clear=node('input');clear.type='checkbox';clear.checked=value===null||value===undefined;
  clearLabel.append(clear,node('span','Clear '+label));wrapper.append(clearLabel);input.disabled=clear.checked;
  clear.onchange=()=>{input.disabled=clear.checked;wrapper.dispatchEvent(new Event('input',{bubbles:true}));};
 }
 return {wrapper,read(){
  if(clear?.checked)return null;
  if(schema.choices){if(input.value==='')throw Error('Choose '+label+'.');return JSON.parse(input.value);}
  if(schema.type==='boolean')return input.value==='true';
  if(['integer','float'].includes(schema.type)){const number=Number(input.value);if(!input.value.trim()||!Number.isFinite(number)||schema.type==='integer'&&!Number.isInteger(number))throw Error('Enter a valid number for '+label+'.');return number;}
  if(schema.type==='json'){try{return JSON.parse(input.value);}catch{throw Error('Enter valid JSON for '+label+'.');}}
  if(schema.required&&!input.value.length)throw Error(label+' is required.');return input.value;
 }};
}
function operatorButton(label,action,style='tertiary'){
 const button=node('button',label,'cds--btn cds--btn--'+style+' cds--layout--size-sm');button.type='button';button.dataset.action='';button.onclick=()=>run(action);return button;
}
function operatorError(code){
 return ({record_changed_reload_before_editing:'This record changed. Close and reopen it before editing.',
  specification_changed_reload_management_options:'The service specification changed. Close the record and reload administration options.',
  fields_not_enabled_for_management:'One or more fields are not permitted for this operator action.',
  data_administration_not_enabled_in_specification:'This service has not enabled data administration in looprd.yaml.',
  invalid_operator_input:'Check the input types and provide a reason for this change.',invalid_operator_values:'One or more values are invalid.',
  value_not_in_choices:'Choose one of the permitted values.',value_outside_bounds:'A value is outside the permitted range.',
  record_constraint_conflict:'The change conflicts with an existing database constraint.',
  operation_id_reused_with_different_input:'This receipt belongs to a different request. Reopen the record and review a new change.',
  management_action_failed_transaction_rolled_back:'The action failed. Its database changes were rolled back; see Diagnostics for failure history.'})[code]||(typeof code==='string'?code.replaceAll('_',' '):code);
}
