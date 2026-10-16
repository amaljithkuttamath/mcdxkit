'use strict';
const $ = id => document.getElementById(id);
let viewerMode = null, currentCalculatedId = null;
let inputMap = null;
let libraryProject=null;
let session, templateId = null, hasTemplate = false, busy = false, step = 1, activeItem = null, currentViewFile = null, currentDocument = null, currentWorksheetId = null;
const items = [];
// On phones the viewer covers the workflow, so it opens only when asked for.
const compactView = window.matchMedia('(max-width:560px)');
let viewerOpener = null, lastAction = null;
// render() rebuilds cards, so remember controls by container and label rather than by node.
function focusKey(node){if(!node||node===document.body||$('inspector').contains(node))return null;return {scope:node.closest('[id]')?.id,label:node.textContent,id:node.id};}
function restoreFocus(key){if(!key)return;const scope=key.id?null:$(key.scope);const node=key.id?$(key.id):Array.from(scope?.querySelectorAll('button,summary,input,select,textarea')||[]).find(n=>n.textContent===key.label);if(node&&!node.disabled)node.focus();}
function overrideProblem(message){$('override-error').textContent=message;$('override-error').hidden=false;$('overrides').setAttribute('aria-invalid','true');$('overrides').closest('details').open=true;}
function closeViewer(){if(!$('inspector').open)return;$('inspector').close();document.body.classList.remove('document-open');restoreFocus(viewerOpener);viewerOpener=null;}
function el(tag, text, cls) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function button(text, action, cls) { const n=el('button',text,cls); n.type='button'; n.addEventListener('click',action); return n; }
function notice(text,error=false) { $('notice').textContent=text; $('notice').className='statusline'+(error?' error':''); }
async function api(path,data,raw=false) {
  const options={headers:{'X-MCDXKit-Token':session.token}};
  if(data!==undefined){options.method='POST';options.headers['Content-Type']=raw?'application/octet-stream':'application/json';options.body=raw?data:JSON.stringify(data);}
  const r=await fetch(path,options);const value=await r.json();if(!r.ok)throw new Error(value.error||'The server could not complete this request.');return value;
}
async function run(fn) { if(busy)return;lastAction=focusKey(document.activeElement);busy=true;$('operation-progress').hidden=true;$('workspace').disabled=true;$('workspace').setAttribute('aria-busy','true');try{await fn();}catch(e){notice(e.message,true);}finally{busy=false;$('workspace').disabled=false;$('workspace').setAttribute('aria-busy','false');render();if(document.activeElement===document.body&&!$('inspector').open)restoreFocus(lastAction);} }

// Progress belongs to this browser operation, never to the worksheet or request.
async function processFiles(label, pending, process) {
  if (!pending.length) return {ready: 0, failed: 0};
  let completed = 0, succeeded = 0;
  const panel = $('operation-progress'), bar = $('operation-bar');
  function update(filename = null) {
    const running = filename !== null;
    const failed = completed - succeeded;
    panel.hidden = false;
    panel.dataset.running = String(running);
    panel.dataset.failed = String(failed > 0);
    $('operation-title').textContent = running ? label
      : completed < pending.length ? 'Operation stopped'
      : failed ? 'Finished with errors' : label + ' complete';
    $('operation-count').textContent = `${completed} of ${pending.length} processed`;
    $('operation-current').textContent = running ? filename
      : `${succeeded} ready` + (failed ? ` · ${failed} failed` : '')
        + (completed < pending.length ? ` · ${pending.length - completed} not processed` : '');
    bar.max = pending.length;
    // The backend returns one result per file; it does not report equation progress.
    if (running && pending.length === 1) bar.removeAttribute('value');
    else bar.setAttribute('value', completed);
  }
  try {
    for (const item of pending) {
      update(item.name);
      if (await process(item)) succeeded++;
      completed++;
    }
  } finally {
    update();
  }
  return {ready: succeeded, failed: completed - succeeded};
}
function setStep(value) { step=value;$('workflow-position').textContent='Step '+step+' of 4';for(let i=1;i<=4;i++)$('step-'+i).hidden=i!==step;document.querySelectorAll('[data-step]').forEach(n=>{n.classList.toggle('active',Number(n.dataset.step)===step);n.classList.toggle('done',Number(n.dataset.step)<step);n.setAttribute('aria-current',Number(n.dataset.step)===step?'step':'false');});render(); }
function overrides() {
  $('override-error').hidden=true;$('overrides').removeAttribute('aria-invalid');
  const values={};
  for(const line of $('overrides').value.split('\n').map(x=>x.trim()).filter(Boolean)){
    const match=line.match(/^([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.+)$/);
    if(!match||!Number.isFinite(Number(match[2]))||Object.hasOwn(values,match[1])){
      const message='Use VARIABLE=NUMBER, one per line, with no repeated variables.';
      overrideProblem(message);
      setStep(2);setTimeout(()=>$('overrides').focus(),0);throw new Error(message);
    }
    values[match[1]]=Number(match[2]);
  }
  return values;
}
function mappingStatus(){const count=Object.keys(inputMap||{}).length;$('template-status').textContent=count?`${count} input${count===1?'':'s'} mapped`:'Choose input mappings in Inputs';}
function mappingOptions(){return inputMap===null?{}:{input_map:inputMap};}
function options(item){return {id:item.id,cases:item.selected,load_source:$('basis').value,template_id:templateId,overrides:overrides(),...mappingOptions()};}
function selectedReports(){return items.filter(i=>i.inspection&&i.selected.length&&i.inspection.envelope);}
function ready(){return selectedReports().filter(i=>i.inspection.template_validation?.status==='ready');}
async function loadTemplateInputs(savedMap){
  inputMap={};invalidate();
  const data=await api('/api/template-inputs',{template_id:templateId,...(savedMap?{input_map:savedMap}:{})});
  inputMap=data.input_map;
  const panel=$('template-inputs'),fields=$('template-input-fields');panel.hidden=false;panel.open=data.mapping_required;
  $('template-inputs-title').textContent=`${data.inputs.length} inputs detected in ${data.name}`;
  mappingStatus();
  fields.replaceChildren();
  for(const row of data.inputs){
    const label=el('label',undefined,'template-input-row'),select=el('select');
    select.setAttribute('aria-label','Report source for '+row.variable);
    for(const [key,title] of [['','Keep template value'],['P','Axial P · kip'],['Vy','Shear y · kip'],['Vz','Shear z · kip'],['My','Moment y · kip-in'],['Mz','Moment z · kip-in']]){
      const option=el('option',title);option.value=key;select.append(option);
    }
    select.value=inputMap[row.variable]||'';select.disabled=row.status==='ambiguous';
    select.addEventListener('change',()=>run(async()=>{if(select.value)inputMap[row.variable]=select.value;else delete inputMap[row.variable];mappingStatus();for(const item of items){item.preview=null;item.result=null;if(item.inspection)delete item.inspection.template_validation;}await refreshAll();}));
    label.append(el('span',row.variable),el('small',`Template: ${row.value} ${row.unit}${row.status==='ambiguous'?' · ambiguous definition':''}`),select);fields.append(label);
  }
}
function invalidate(){for(const item of items){item.preview=null;if(item.inspection)delete item.inspection.template_validation;}}
async function refresh(item){
  item.preview=null;item.error=null;
  if(item.inspection)delete item.inspection.template_validation;
  try{
    const request={...options(item),cases:item.selected.length?item.selected:undefined};
    item.inspection=await api('/api/inspect',request);
    if(!item.selected.length){item.inspection.envelope=null;if(item.inspection.template_validation?.status==='ready')item.inspection.template_validation.status='needs_cases';item.error='Select at least one load case.';}
  }catch(e){item.error=e.message;item.inspection.envelope=null;item.inspection.governing=null;item.inspection.review_checks=null;}
}
async function refreshAll(){for(const item of items.filter(i=>i.inspection))await refresh(item);render();}
function pairValidation(found){
  const panel=el('section',undefined,'template-validation'),check=found.template_validation;
  panel.append(el('h4',check?.status==='ready'?'Template and report inputs checked':'Template and report need attention'));
  if(!check){panel.append(el('p','Validate the template with this report before reviewing changes.'));return panel;}
  for(const message of check.errors||[])panel.append(el('p',message,'error-text'));
  if(check.status==='needs_mapping')panel.append(el('p','Choose report sources for the detected template inputs below.'));
  if(check.status==='needs_cases')panel.append(el('p','Select the intended load cases to finish validation.'));
  for(const row of found.template_inputs?.inputs||[]){if(row.replacement)panel.append(el('p',`${row.variable} ← ${row.component}: ${row.replacement.value} ${row.replacement.unit}`));}
  for(const message of check.warnings||[])panel.append(el('p',message,'small'));
  panel.append(el('small','Checks input compatibility; equations calculate in the next step. Engineering approval and Prime execution remain separate.'));
  return panel;
}

async function download(file){const r=await fetch('/api/download/'+file.id,{headers:{'X-MCDXKit-Token':session.token}});if(!r.ok)throw new Error((await r.json()).error);const url=URL.createObjectURL(await r.blob());const a=el('a');a.href=url;a.download=file.name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
function removeButton(item){const remove=button('×',()=>{items.splice(items.indexOf(item),1);render();},'quiet');remove.setAttribute('aria-label','Remove '+item.name);return remove;}
function head(item,label){const h=el('div',undefined,'card-head'),title=el('div');title.append(el('h3',item.name));if(item.relativePath&&item.relativePath!==item.name)title.append(el('p',item.relativePath,'card-path'));title.append(el('span',label,'tag'));h.append(title);return h;}
function diffView(value,all=false,envelope=null,mapping={}){const fragment=el('div');const changed=value.changes.filter(c=>c.kind!=='added');const added=value.changes.filter(c=>c.kind==='added');const h=el('div',undefined,'diff-header');h.append(el('span','Original template'),el('span','New worksheet'));fragment.append(h);for(const c of (all?value.changes:changed)){const row=el('div',undefined,'diff-row');row.append(el('pre',c.before||'— New native expression','diff-before'),el('pre',(c.after||'— Removed')+(envelope&&mapping[c.variable]?'\nReport preview: '+envelope[mapping[c.variable]]+(mapping[c.variable].startsWith('M')?' kip-in':' kip'):''),'diff-after'));fragment.append(row);}fragment.append(el('p',`${changed.length} changed expressions and ${added.length} new native input/envelope expressions`,'diff-summary'));if(!all&&added.length){const details=el('details');details.append(el('summary',`See ${added.length} added expressions`));for(const c of added)details.append(el('pre',c.after,'source-text'));fragment.append(details);}return fragment;}
function validation(parent,value,calculation=null){const status=el('div',undefined,'result-status');status.append(el('span','✓ Package structure checked'),el('span',calculation?.calculated?'✓ Calculated with CalcpadCE':'Mathcad execution unverified','pending'));parent.append(status,el('p',`${value.math_regions} math regions · ${value.cached_results} cached results · ${value.package_parts} package parts`,'validation-stats'));}
function render(){
  document.querySelectorAll('[data-step]').forEach(n=>{const target=Number(n.dataset.step);n.disabled=(target===2&&!items.length)||(target===3&&!items.some(i=>i.preview||i.previewError))||(target===4&&!items.some(i=>i.result));});
  $('view-calculation').disabled=!currentCalculatedId&&!activeItem?.preview&&!activeItem?.result;
  $('view-report').disabled=!activeItem?.id;$('view-template').disabled=!hasTemplate;$('view-worksheet').disabled=!activeItem?.preview&&!activeItem?.result&&!currentWorksheetId;$('view-diff').disabled=$('view-worksheet').disabled;
  $('file-count').textContent=items.length+' FILE'+(items.length===1?'':'S');$('count').textContent=items.length;
  $('queue-summary').textContent=ready().length+' ready';
  $('download-summary').hidden=items.filter(i=>i.inspection).length<2;$('download-summary').disabled=selectedReports().length<2;
  $('review-next').disabled=!items.length;$('compare-next').disabled=!hasTemplate||!ready().length;
  $('convert-all').disabled=!items.some(i=>i.preview&&!i.result);
  $('inspect-template').disabled=!hasTemplate;$('choose-template').textContent=hasTemplate?'Change template':'Choose template';$('inspect-template-review').disabled=!hasTemplate;
  const imports=$('import-list');imports.replaceChildren();
  for(const item of items){const row=el('div',undefined,'import-row');row.append(el('span',item.kind==='report'?'REPORT':'MCDX'),el('b',item.name),el('span',item.error?'Needs attention':'Added','tag'+(item.error?' error':'')),removeButton(item));imports.append(row);}
  const queue=$('queue');queue.replaceChildren();if(!items.length)queue.append(el('div','Add a report to check its inputs.','empty'));
  for(const item of items){const card=el('article',undefined,'card'),heading=head(item,item.inspection?'Local load envelope':item.error?'Needs attention':'Worksheet');if(item.kind==='report'&&hasTemplate&&item.selected.length)heading.append(button(libraryProject?.report_sha256?'View project design basis':libraryProject?'Connect to project library':'Create design package',()=>run(async()=>{if(libraryProject?.report_sha256){location.href='/documents?project='+libraryProject.id;return;}const settings=options(item);if(Object.keys(settings.overrides||{}).length)throw new Error('Save overrides in a new template, or review document-backed suggestions before connecting.');const project=await api(libraryProject?'/api/documents/connect':'/api/documents/create',{...settings,...(libraryProject?{project:libraryProject.id}:{title:item.name.replace(/\.[^.]+$/,'')})});location.href='/documents?project='+project.id;})));card.append(heading);
    if(item.inspection){card.append(pairValidation(item.inspection));const caseDetails=el('details',undefined,'load-cases');caseDetails.open=item.casesOpen??Boolean(item.inspection.selection_required);caseDetails.addEventListener('toggle',()=>{item.casesOpen=caseDetails.open;});caseDetails.append(el('summary',`${item.selected.length} of ${item.inspection.cases.length} load cases selected`),el('p',item.inspection.summary_only?'Summary-only report: case names are absent. Choose the load cases to include.':item.inspection.selection_required?'Choose the load cases to include.':'STR cases selected by default.','case-caption'));const cases=el('div',undefined,'cases');for(const c of item.inspection.cases){const label=el('label',undefined,'case-label'),input=el('input');input.type='checkbox';input.checked=item.selected.includes(c.id);input.addEventListener('change',()=>run(async()=>{item.selected=input.checked?[...item.selected,c.id].sort((a,b)=>a-b):item.selected.filter(id=>id!==c.id);await refresh(item);}));label.append(input,document.createTextNode(`${c.id}: ${c.name||'Unnamed'}`));if(dominanceOf(item.inspection.review_checks)?.dominated?.some(x=>x.case===c.id)){label.classList.add('dominated');label.title='Never governs any envelope component (advisory hint; selection unchanged)';}cases.append(label);}caseDetails.append(cases);card.append(caseDetails);
      if(item.inspection.envelope){const values=el('div',undefined,'envelope');for(const [key,value]of Object.entries(item.inspection.envelope)){const cell=el('div',undefined,'measure');const label=el('span',undefined,'measure-label');const gov=item.inspection.governing?.[key];label.append(el('span',({P:'Axial load',Vy:'Shear y',Vz:'Shear z',My:'Moment y',Mz:'Moment z'})[key]||key),el('small',gov?`${key} · case ${gov.case}${gov.case_name?' '+gov.case_name:''}${gov.lead_ratio&&gov.lead_ratio>=1.5?` · ${gov.lead_ratio.toFixed(1)}× next case`:''}`:key));cell.append(label,el('strong',Number(value.toPrecision(6)).toString()),el('small',key.startsWith('M')?'kip-in':'kip'));values.append(cell);}card.append(values);}
      {const note=dominanceView(item.inspection.review_checks,item.inspection.cases);if(note)card.append(note);}
    }
    if(item.inspection?.review_checks?.flags?.length||item.inspection?.review_checks?.errors?.length)card.append(reviewView(item.inspection.review_checks));
    if(item.advice)card.append(adviceView(item));
    if(item.error)card.append(el('p',item.error,'error-text'));
    if(item.validation)validation(card,item.validation);
    if(item.id){const actions=el('div',undefined,'card-actions');actions.append(button(item.kind==='report'?'Open report':'Open file',()=>run(()=>inspect(item.id))));if(item.kind==='report'&&item.inspection)actions.append(button('Suggest cases',()=>run(async()=>{const fresh=await api('/api/inspect',{id:item.id,cases:item.selected.length?item.selected:undefined,load_source:$('basis').value});if(!fresh.case_suggestions){notice('Case suggestions are turned off on this server (MCDXKIT_CHECKS).',true);return;}item.advice=fresh.case_suggestions;notice('Review the suggested cases before using them.');})));if(item.validation)actions.append(button('Validate again',()=>run(async()=>{item.validation=await api('/api/validate',{id:item.id});notice('Package checks passed. Native execution requires Mathcad.');})));;card.append(actions);}queue.append(card);
  }
  const comparisons=$('comparisons');comparisons.replaceChildren();let count=0;
  for(const item of items){if(!item.preview&&!item.previewError)continue;count++;const card=el('article',undefined,'card');card.append(head(item,item.preview?'Ready to save':'Needs attention'));if(item.preview){const changes=item.preview.diff.changes;card.append(el('p',`${changes.filter(c=>c.kind!=='added').length} updated expressions and ${changes.filter(c=>c.kind==='added').length} added expressions`,'small'));if(item.preview.review_checks?.flags?.length||item.preview.review_checks?.errors?.length)card.append(reviewView(item.preview.review_checks,3));const actions=el('div',undefined,'card-actions');actions.append(button('View changes',()=>run(()=>inspectDiff(item.preview.worksheet.id))),button('Open proposed file',()=>run(()=>inspect(item.preview.worksheet.id))));card.append(actions);}else card.append(el('p',item.previewError,'error-text'));comparisons.append(card);}if(!count)comparisons.append(el('div','Check your inputs first, then select Review changes.','empty'));
  const results=$('results');results.replaceChildren();for(const item of items.filter(i=>i.previewError)){const failed=el('article',undefined,'card');failed.append(head(item,'Generation needs attention'),el('p',item.previewError,'error-text'));results.append(failed);}for(const item of items.filter(i=>i.result))results.append(resultCard(item.result,item.name));if(!items.some(i=>i.result))results.append(el('div','Your files will appear here. Earlier conversions are in Saved outputs.','empty'));
}
function resultCard(result,name){
  const card=el('article',undefined,'card output-card');card.append(head({name:result.worksheet.name||name},result.calculation?.calculated?'Calculated files ready':'Files ready'));
  const actions=el('div',undefined,'card-actions');actions.append(button('Open file',()=>run(()=>inspect(result.worksheet.id)),'primary-button'));
  if(result.calculated_worksheet)actions.append(button('Calculated results',()=>run(()=>inspect(result.worksheet.id,'calculated'))));
  if(result.audit)actions.append(button('Sources & standards',()=>run(()=>inspectStandards(result.worksheet.id))));
  actions.append(button('Download Mathcad',()=>run(()=>download(result.worksheet))));
  if(result.open_worksheet)actions.append(button('Download Calcpad',()=>run(()=>download(result.open_worksheet))));
  card.append(checkPanel(result),actions);const details=el('details');details.append(el('summary','File details'));
  if(result.validation)validation(details,result.validation,result.calculation);details.append(el('p',result.output,'path'));
  const more=el('div',undefined,'card-actions');more.append(button('View changes',()=>run(()=>inspectDiff(result.worksheet.id))),button('Download audit',()=>run(()=>download(result.audit))),button('Check file structure',()=>run(async()=>{result.validation=await api('/api/validate',{id:result.worksheet.id});notice('File structure checked. Native calculation requires Mathcad.');})));
  if(result.calculated_worksheet)more.append(button('Download results page',()=>run(()=>download(result.calculated_worksheet))));details.append(more);card.append(details);return card;
}
const componentNames={P:'Axial load',Vy:'Shear y',Vz:'Shear z',My:'Moment y',Mz:'Moment z',V:'Lateral resultant'};
const ruleNames={service_axial_above_strength:'Service above strength',effects_below_top:'Effects below pile top',duplicate_case:'Duplicate case',ratio_outlier:'Unusual ratio',gross_magnitude:'Possible unit slip'};
function reviewRow(f){const row=el('li');row.append(el('span',ruleNames[f.rule]||f.rule,'advice-tag review'),el('b',`Review: case ${f.case}`+(f.component?` · ${componentNames[f.component]||f.component}`:'')),el('small',f.message));return row;}
function reviewView(review,limit=8){
  const box=el('section',undefined,'advice review-flags');box.setAttribute('aria-label','Review flags');
  const flags=review.flags||[];
  if(review.errors?.length&&!flags.length){box.append(el('h4','Review: unavailable · advisory'),el('p',`Some advisory checks could not run (${review.errors.map(e=>(e.id?e.id+': ':'')+e.message).join('; ')}). Inputs and results are unaffected; check the GROUP input yourself.`,'small'));return box;}box.append(el('h4',`Review: ${flags.length} item${flags.length===1?'':'s'} to check · advisory`),el('p','Consistency checks on the GROUP summary. They never change inputs, case selection or results, and are not a pass or fail. Check the GROUP input for each item.','small'));
  const list=el('ul',undefined,'advice-list');for(const f of flags.slice(0,limit))list.append(reviewRow(f));box.append(list);
  if(flags.length>limit){const more=el('details');more.append(el('summary',`Show ${flags.length-limit} more`));const rest=el('ul',undefined,'advice-list');for(const f of flags.slice(limit))rest.append(reviewRow(f));more.append(rest);box.append(more);}
  if(review.skipped?.length)box.append(el('p','Not run: '+review.skipped.map(s=>`${ruleNames[s.id]||s.id} (${s.reason})`).join('; ')+'.','small'));
  if(review.errors?.length)box.append(el('p','Could not run: '+review.errors.map(e=>`${ruleNames[e.id]||e.id||'review'} (${e.message})`).join('; ')+'.','small'));
  return box;
}
function dominanceOf(review){return review?.annotations?.find(a=>a.kind==='dominance')?.data;}
function dominanceView(review,cases){
  const d=dominanceOf(review);if(!d?.dominated?.length)return null;const name=id=>{const c=cases.find(x=>x.id===id);return `${id}${c?.name?' '+c.name:''}`;};
  const box=el('div',undefined,'dominance');box.append(el('p',(d.basis==='selected'?'Never governs (advisory hint; cases stay selected): ':'Never governs, on a review-only fallback basis of all cases except extreme-event names (not a selection): ')+d.dominated.map(x=>`case ${name(x.case)} (≤ case ${x.dominated_by.join(', ')} in every component)`).join('; ')+'.','small dominance-note'));return box;
}
function adviceView(item){
  const a=item.advice,box=el('section',undefined,'advice');box.setAttribute('aria-label','Suggested load cases');box.append(el('h4','Suggested cases · local model, advisory'),el('p',`Classified from case names by a model trained on ${a.trained_on.seed} common names`+(a.trained_on.history?` and ${a.trained_on.history} case names from your earlier conversions.`:'. It learns your naming from each conversion you complete.'),'small'));
  const list=el('ul',undefined,'advice-list');for(const c of a.cases){const row=el('li');row.append(el('span',c.category,'advice-tag '+c.category),el('b',`${c.id}: ${c.name||'Unnamed'}`),el('small',`${Math.round(c.confidence*100)}% confident`+(c.evidence.length?` · matched “${c.evidence.join('”, “')}”`:'')));list.append(row);}box.append(list);
  if(a.unresolved_case_ids.length)box.append(el('p',`Not confident about case ${a.unresolved_case_ids.join(', ')}. Decide these yourself.`,'small'));
  if(a.selection_issue)box.append(el('p','The suggestion cannot be used as is: '+a.selection_issue,'error-text'));
  const actions=el('div',undefined,'card-actions');if(a.recommended_cases.length&&!a.selection_issue)actions.append(button(`Use cases ${a.recommended_cases.join(', ')}`,()=>run(async()=>{item.selected=[...a.recommended_cases];item.advice=null;await refresh(item);notice('Suggested cases selected. Check the envelope before continuing.');}),'primary-button'));
  actions.append(button('Dismiss',()=>{item.advice=null;render();},'quiet'));box.append(actions);return box;
}
function ratioText(value){return value===null||value===undefined?'—':Number(value).toFixed(3);}
function checkPanel(result){
  // Outcomes are read by the server from the calculated CalcpadCE page; nothing is recalculated here.
  const summary=result.check_summary,panel=el('section',undefined,'check-panel');panel.setAttribute('aria-label','Worksheet checks');
  const head=el('div',undefined,'check-head');head.append(el('h4','Worksheet checks'));panel.append(head);
  if(!summary){head.append(el('span','Not recorded','check-state none'));panel.append(el('p','This output was created before check results were recorded. Open Calculated results to review it.','check-note'));return panel;}
  if(!summary.total){head.append(el('span','No checks found','check-state none'));panel.append(el('p','No rendered comparisons in the calculated page. Review the calculated results directly.','check-note'));return panel;}
  head.append(el('span',summary.failed?`${summary.failed} failed`:`All ${summary.total} passed`,'check-state '+(summary.failed?'fail':'pass')));
  const stats=el('dl',undefined,'check-stats');const stat=(label,value)=>{const cell=el('div');cell.append(el('dt',label),el('dd',value));stats.append(cell);};
  stat('Governing D/C',summary.governing?ratioText(summary.governing.ratio):'—');stat('Governing check',summary.governing?.name||'No ratio form');stat('Passed',`${summary.passed} of ${summary.total}`);panel.append(stats);
  const list=(rows)=>{const ul=el('ul',undefined,'check-list');for(const c of rows){const li=el('li',undefined,c.passed?'pass':'fail');li.append(el('span',c.passed?'Pass':'Fail','check-mark'),el('b',c.name),el('span',ratioText(c.ratio),'check-ratio'),el('code',c.expression,'check-expression'));li.title=c.substituted;ul.append(li);}return ul;};
  if(result.checks){const failed=result.checks.filter(c=>!c.passed);if(failed.length)panel.append(list(failed));
    if(result.checks.length){const all=el('details',undefined,'check-details');all.append(el('summary',`All ${result.checks.length} checks`),list(result.checks));panel.append(all);}}
  else{
    // Saved outputs carry the summary only; expressions and ratios are in the downloadable audit.
    if(summary.failed_checks.length){const ul=el('ul',undefined,'check-list');for(const name of summary.failed_checks){const li=el('li',undefined,'fail');li.append(el('span','Fail','check-mark'),el('b',name));ul.append(li);}panel.append(ul);}
    panel.append(el('p','Download the audit for every check expression and ratio.','check-note'));
  }
  panel.append(el('p','Conditions as rendered by CalcpadCE (1 = pass, 0 = fail). Not an engineering approval.','check-note'));return panel;

}
function markView(mode){viewerMode=mode;for(const name of ['report','template','worksheet','calculation','diff'])$('view-'+name).setAttribute('aria-current',String(name===mode));}
function dialog(title,type,note){if(!$('inspector').open)viewerOpener=focusKey(document.activeElement)||lastAction;markView(null);$('preview-summary').textContent='About this preview';$('inspector-title').textContent=title;$('inspector-type').textContent=type;$('inspector-note').textContent=note;$('inspector-content').replaceChildren();$('inspector-content').className='';currentDocument=null;$('inspector-search').value='';$('inspector-search').hidden=false;document.body.classList.add('document-open');if(!$('inspector').open)$('inspector').show();}
async function inspect(id,mode='file'){currentViewFile=id;currentDocument=null;const found=items.find(i=>i.id===id||i.preview?.worksheet.id===id||i.result?.worksheet.id===id);if(found)activeItem=found;const data=await api('/api/view',{id});if(data.kind!=='report'&&id!==(templateId||'default-template'))currentWorksheetId=id;dialog(data.name,data.kind==='report'?'ORIGINAL SOURCE':'WORKSHEET INSPECTOR',data.kind==='report'?'Original report text. Search or scroll to inspect the source.':'Text and native equations extracted from the .mcdx. This is not a native Mathcad page rendering; no expressions are executed.');const body=$('inspector-content');markView(data.kind==='report'?'report':id===(templateId||'default-template')?'template':'worksheet');$('preview-summary').textContent=data.kind==='report'?'Original report text':'About this file';if(data.calculated_html)currentCalculatedId=id;$('view-calculation').disabled=!currentCalculatedId;if(data.calculated_html&&mode==='calculated'){markView('calculation');$('preview-summary').textContent='Calculated with CalcpadCE';$('inspector-title').textContent=data.name.replace(/\.mcdx$/i,'.cpd');$('inspector-type').textContent='CALCULATED WORKSHEET';$('inspector-note').textContent='Calculated locally with CalcpadCE. This page is a result snapshot; the .cpd and .mcdx files contain the formulas. Prime execution remains unverified.';const calculated=el('div',undefined,'calculated');appendCalculated(calculated,data.calculated_html);body.append(calculated);return;}if(data.kind==='report'){const lines=data.text.split('\n');for(let n=0;n<lines.length;n+=100){const block=el('pre',lines.slice(n,n+100).map((line,i)=>String(n+i+1).padStart(5)+'  '+line).join('\n'),'source-text');block.dataset.search=block.textContent.toLowerCase();body.append(block);}}else{renderDocument(data,body);return;}}
async function inspectDiff(id){currentWorksheetId=id;const data=await api('/api/diff',{id});dialog('Changes from template','EXPRESSION DIFF',data.scope);markView('diff');$('preview-summary').textContent='Changed equations and inputs';$('inspector-search').hidden=true;const item=items.find(i=>i.preview?.worksheet.id===id||i.result?.worksheet.id===id);$('inspector-content').append(diffView(data,false,item?.preview?.envelope||item?.result?.envelope,item?.preview?.input_map||item?.result?.input_map||{}));}
function downloadRegister(register){
  const url=URL.createObjectURL(new Blob([JSON.stringify(register,null,2)+'\n'],{type:'application/json'}));
  const link=el('a');link.href=url;link.download='engineering-register.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
}
async function inspectStandards(id,imported){
  const data=await api('/api/standards',{id,...(imported===undefined?{}:{register:imported})});
  dialog('Sources & standards','ENGINEERING REGISTER','Source records and declared reviews. This does not certify the design or verify a reviewer’s identity.');
  $('preview-summary').textContent='Provenance and review records';$('inspector-search').hidden=true;
  const body=$('inspector-content');body.className='standards-view';
  const {register,assessment}=data, state=value=>value.replaceAll('_',' ');
  const intro=el('section',undefined,'standards-section');intro.append(el('h3',state(assessment.status)),el('p',`Revision ${register.revision} · ${register.rules.length} rule records`));
  intro.append(el('p','Imported loads are linked below. Geometry, materials and code factors inherited from the template still need project-specific sources and review.'));
  const actions=el('div',undefined,'card-actions'),file=el('input');file.type='file';file.accept='.json';file.hidden=true;
  file.addEventListener('change',()=>{const selected=file.files[0];file.value='';if(selected)run(async()=>{if(selected.size>1024*1024)throw new Error('Register exceeds 1 MiB.');await inspectStandards(id,JSON.parse(await selected.text()));});});
  actions.append(button('Download register',()=>downloadRegister(register)),button('Load register',()=>file.click()),file);intro.append(actions);body.append(intro);
  const basis=el('details',undefined,'standards-section');basis.open=false;basis.append(el('summary',`Project basis · ${assessment.missing_basis.length} missing`));
  const fields=el('dl');for(const [key,value] of Object.entries(register.project_basis))fields.append(el('dt',state(key)),el('dd',value||'Not provided'));basis.append(fields);body.append(basis);
  for(const value of register.values){
    const section=el('section',undefined,'standards-section'),source=register.sources.find(s=>s.id===value.source_id),finding=assessment.values.find(v=>v.id===value.id);
    section.append(el('h3',`${value.id} · ${value.quantity} ${value.unit}`),el('p',`${source?.reference||'Missing source'} · source version ${value.source_version||'missing'}`));
    section.append(el('p',value.template_mapping?`Worksheet input: ${value.template_mapping}`:'Retained for inspection; not mapped to a worksheet input.'));
    for(const note of value.transformations)section.append(el('p',String(note)));
    section.append(el('p',`${state(finding.state)} · ${finding.reason}`));
    const evidence=el('details');evidence.append(el('summary','Source evidence'),el('pre',JSON.stringify({source,locator:value.source_locator},null,2)));section.append(evidence);
    const rules=assessment.rules.filter(r=>r.value_ids.includes(value.id));
    if(!rules.length)section.append(el('p','No reviewed engineering rule linked.'));
    for(const rule of rules){const record=register.rules.find(r=>r.id===rule.id),authority=register.sources.find(r=>r.id===rule.source_id);section.append(el('h4',`${rule.id} · ${state(rule.state)}`),el('p',`${authority?.reference||'Missing authority'} · ${authority?.edition||'Missing edition'} · ${rule.clause||'Missing clause'}`),el('p',rule.reason));const detail=el('details');detail.append(el('summary','Rule and review record'),el('pre',JSON.stringify(record,null,2)));section.append(detail);}
    body.append(section);
  }
  if(register.overrides.length){const overrides=el('section',undefined,'standards-section');overrides.append(el('h3','Template overrides'));for(const value of register.overrides)overrides.append(el('p',`${value.variable} = ${value.quantity} · ${state(value.review_state)} · ${value.reason||'Reason missing'}`));body.append(overrides);}
}
async function history(){const data=await api('/api/history');dialog('Saved outputs','OUTPUT HISTORY','Previously generated files. Open a file to inspect it or download it.');$('preview-summary').textContent='Previous conversions';$('inspector-search').hidden=true;for(const result of data)$('inspector-content').append(resultCard(result,result.worksheet.name));if(!data.length)$('inspector-content').append(el('div','No saved outputs yet.','empty'));}
async function importFiles(files){let added=0,failed=0,skipped=0;for(const file of files){const ext=file.name.split('.').pop().toLowerCase();if(!['gp11t','txt','mcdx'].includes(ext)){skipped++;continue;}if(items.length>=100)throw new Error('The queue is limited to 100 files. Start a new session for more.');const item={name:file.name,relativePath:file.webkitRelativePath,kind:ext==='mcdx'?'worksheet':'report'};notice('Reading '+file.name+'…');try{if(file.size>(item.kind==='report'?16:32)*1024*1024)throw new Error('File exceeds the upload size limit.');Object.assign(item,await api('/api/upload?'+new URLSearchParams({name:file.name,kind:item.kind}),file,true));if(item.inspection){item.selected=item.inspection.selected_cases;item.error=item.inspection.selection_required?(item.inspection.summary_only?'Select the load cases to include in Inputs.':item.inspection.selection_required):null;if(hasTemplate)await refresh(item);}added++;}catch(e){item.error=e.message;failed++;}items.push(item);activeItem=item;render();}if(activeItem?.id&&!compactView.matches)await inspect(activeItem.id);notice(`${added} file${added===1?'':'s'} added`+(failed?` · ${failed} failed`:'')+(skipped?` · ${skipped} unsupported files skipped`:'')+'.',failed>0);}
for(const id of ['files','folder']){$('choose-'+(id==='files'?'files':'folder')).addEventListener('click',()=>$(id).click());$(id).addEventListener('change',e=>{const files=Array.from(e.target.files);e.target.value='';run(()=>importFiles(files));});}
$('choose-template').addEventListener('click',()=>$('template').click());
$('template').addEventListener('change',e=>{const file=e.target.files[0];e.target.value='';if(!file)return;run(async()=>{if(file.size>32*1024*1024)throw new Error('Template exceeds 32 MiB.');const value=await api('/api/upload?'+new URLSearchParams({name:file.name,kind:'template'}),file,true);templateId=value.id;hasTemplate=true;invalidate();$('template-name').textContent=value.name;$('template-status').textContent='Reading inputs…';for(const item of items)item.result=null;await loadTemplateInputs();await refreshAll();await inspect(templateId);notice('Template selected.');});});
for(const id of ['inspect-template','inspect-template-review'])$(id).addEventListener('click',()=>run(()=>inspect(templateId||'default-template')));
$('basis').addEventListener('change',()=>run(async()=>{for(const item of items.filter(i=>i.inspection)){item.advice=null;await refresh(item);}notice('Local load source updated. Review the refreshed envelopes.');}));
$('overrides').addEventListener('input',()=>{$('override-error').hidden=true;$('overrides').removeAttribute('aria-invalid');invalidate();for(const item of items){if(item.inspection)delete item.inspection.template_validation;}render();});
$('overrides').addEventListener('change',()=>run(refreshAll));
$('review-next').addEventListener('click',()=>run(async()=>{setStep(2);await refreshAll();notice('Template and report inputs checked. Resolve any items shown before reviewing changes.');}));
async function downloadSummary(){const included=selectedReports(),excluded=items.filter(i=>i.inspection&&!included.includes(i));const unselected=excluded.filter(i=>!i.selected.length).length,failed=excluded.length-unselected;const value=await api('/api/summary',{reports:included.map(i=>({id:i.id,cases:i.selected})),load_source:$('basis').value});const url=URL.createObjectURL(new Blob(['\ufeff'+value.csv],{type:'text/csv;charset=utf-8'}));const a=el('a');a.href=url;a.download=value.name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);const plural=n=>n+' report'+(n===1?'':'s');notice(`Summary downloaded: ${plural(value.reports)}, ${value.rows} load cases.`+(unselected?` Not included: ${plural(unselected)} without a load case selection.`:'')+(failed?` Not included: ${plural(failed)} whose load refresh failed; see the report card.`:'')+(value.duplicates_skipped.length?` Identical duplicates skipped: ${value.duplicates_skipped.join(', ')}.`:''));}
$('download-summary').addEventListener('click',()=>run(downloadSummary));
$('compare-next').addEventListener('click', () => run(async () => {
  const pending = ready();
  let rejected = null;
  overrides(); // Validate before showing progress or sending a request.
  setStep(3);
  const result = await processFiles('Preparing previews', pending, async item => {
    notice('Preparing changes for ' + item.name + '…');
    item.previewError = null;
    try {
      item.preview = await api('/api/preview', options(item));
      item.result = null;
    } catch (error) {
      item.preview = null;
      item.previewError = error.message;
      if (/override/i.test(error.message)) rejected = error.message;
    }
    render();
    return Boolean(item.preview);
  });
  if (rejected) {
    overrideProblem(rejected); setStep(2); setTimeout(() => $('overrides').focus(), 0);
    throw new Error('Fix the template input overrides: ' + rejected);
  }
  const prepared = pending.find(item => item.preview);
  if (prepared) { activeItem = prepared; if (!compactView.matches) await inspectDiff(prepared.preview.worksheet.id); }
  notice(result.failed ? `${result.ready} previews ready · ${result.failed} failed. Review the errors.`
    : 'Review the changes, then select Create outputs.', result.failed > 0);
}));
$('convert-all').addEventListener('click', () => run(async () => {
  const pending = items.filter(item => item.preview && !item.result);
  overrides();
  const result = await processFiles('Generating outputs', pending, async item => {
    notice('Generating ' + item.name + '…');
    item.previewError = null;
    try { item.result = await api('/api/convert', options(item)); }
    catch (error) { item.previewError = error.message; }
    render();
    return Boolean(item.result);
  });
  setStep(4);
  const generated = pending.find(item => item.result);
  if (generated) { activeItem = generated; if (!compactView.matches) await inspect(generated.result.worksheet.id); }
  notice(`${result.ready} output${result.ready === 1 ? '' : 's'} created`
    + (result.failed ? ` · ${result.failed} failed` : '') + '.', result.failed > 0);
}));

for(const n of document.querySelectorAll('[data-step]'))n.addEventListener('click',()=>setStep(Number(n.dataset.step)));
for(const n of document.querySelectorAll('[data-back]'))n.addEventListener('click',()=>setStep(Number(n.dataset.back)));
for(const id of ['open-history','refresh-history'])$(id).addEventListener('click',()=>{if(session)run(history);});
$('close-inspector').addEventListener('click',closeViewer);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('inspector').open){e.preventDefault();closeViewer();}});
$('inspector-search').addEventListener('input',()=>{const query=$('inspector-search').value.toLowerCase();if(currentDocument){currentDocument.search(query);return;}const body=$('inspector-content');const rows=body.querySelector('.calculated')?.children||body.children;for(const row of rows)row.hidden=Boolean(query)&&!(row.dataset.search||row.textContent.toLowerCase()).includes(query);});
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,e=>{e.preventDefault();if(!busy)$('dropzone').classList.add('dragging');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');});
$('dropzone').addEventListener('drop',e=>{if(busy)return;const entries=Array.from(e.dataTransfer.items||[]).map(i=>i.webkitGetAsEntry?.());if(entries.some(e=>e?.isDirectory)){notice('Use Add folder to include files from a directory.',true);return;}const files=Array.from(e.dataTransfer.files);run(()=>importFiles(files));});
async function connect(){const r=await fetch('/api/session');if(r.status===401){$('login').hidden=false;notice('Sign in to continue.');return;}if(!r.ok)throw new Error('Cannot connect. Use the URL printed by mcdxkit serve.');session=await r.json();hasTemplate=Boolean(session.template);$('login').hidden=true;$('template-name').textContent=session.template||'No template selected';$('template-status').textContent=session.template?'Ready to use':'Choose a .mcdx file with your design equations';$('output-dir').textContent=session.output_dir;$('storage-note').textContent=session.network?'Selected files upload to this server.':'Files stay on this computer.';$('workspace').disabled=false;notice('Add a report to start.');if(hasTemplate)await loadTemplateInputs();const libraryId=new URLSearchParams(location.search).get('library');if(libraryId){libraryProject=await api('/api/documents/show',{project:libraryId});$('library-context').hidden=false;$('library-context').textContent='Project: '+libraryProject.title;$('nav-documents').href='/documents?project='+libraryId;$('nav-calculations').href='/?library='+libraryId;if(libraryProject.report_sha256){const linked=await api('/api/documents/calculation',{project:libraryId});templateId=linked.template.id;hasTemplate=true;$('template-name').textContent=libraryProject.template_name||linked.template.name;$('basis').value=linked.options.load_source;await loadTemplateInputs(linked.options.input_map);const item={...linked.report,name:libraryProject.report_name||linked.report.name,selected:linked.options.cases};items.push(item);activeItem=item;await refresh(item);setStep(2);notice('Project calculation restored. Check the mapped inputs and selected cases.');}else notice('Add calculation files to connect them to '+libraryProject.title+'.');}render();if(hasTemplate&&!compactView.matches)await inspect(templateId||'default-template');}
$('sign-in').addEventListener('click',async()=>{try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_token:$('access-token').value})});$('access-token').value='';if(!r.ok)throw new Error((await r.json()).error);await connect();}catch(e){notice(e.message,true);}});
connect().catch(e=>notice(e.message,true));

function mathNode(value){const allowed=['mrow','mi','mn','mo','mtext','msub','msup','mfrac','msqrt','mroot','mtable','mtr','mtd'];const node=document.createElementNS('http://www.w3.org/1998/Math/MathML',allowed.includes(value.tag)?value.tag:'mtext');if(value.text!==null&&value.text!==undefined)node.textContent=value.text;for(const child of value.children||[])node.append(mathNode(child));return node;}
$('view-template').addEventListener('click',()=>run(()=>inspect(templateId||'default-template')));
$('view-report').addEventListener('click',()=>run(async()=>{if(activeItem?.id)await inspect(activeItem.id);else notice('Add a report first.');}));
$('view-worksheet').addEventListener('click',()=>run(async()=>{const file=activeItem?.result?.worksheet||activeItem?.preview?.worksheet||(currentWorksheetId?{id:currentWorksheetId}:null);if(file)await inspect(file.id);else notice('Select Review changes to prepare the output file.');}));
$('view-diff').addEventListener('click',()=>run(async()=>{const file=activeItem?.result?.worksheet||activeItem?.preview?.worksheet||(currentWorksheetId?{id:currentWorksheetId}:null);if(file)await inspectDiff(file.id);else notice('Select Review changes to compare the inputs.');}));

function appendCalculated(parent,html){
  const parsed=new DOMParser().parseFromString(html,'text/html');
  const allowed=new Set(['DIV','P','SPAN','VAR','SUP','SUB','I','B','STRONG','EM','H1','H2','BR']);
  function copy(node){if(node.nodeType===Node.TEXT_NODE)return document.createTextNode(node.textContent);if(node.nodeType!==Node.ELEMENT_NODE||!allowed.has(node.tagName))return document.createTextNode('');const fresh=document.createElement(node.tagName.toLowerCase());if(node.className)fresh.className=node.className;for(const child of node.childNodes)fresh.append(copy(child));return fresh;}
  const wrapper=parsed.body.firstElementChild;for(const node of (wrapper?.tagName==='DIV'?wrapper.childNodes:parsed.body.childNodes))parent.append(copy(node));
}

$('view-calculation').addEventListener('click',()=>run(async()=>{const id=activeItem?.result?.worksheet.id||activeItem?.preview?.worksheet.id||currentCalculatedId;if(id)await inspect(id,'calculated');}));
function flowNode(value){
  const allowed=['div','p','span','b','i','u','br','ul','li','sub','sup'];const n=el(allowed.includes(value.tag)?value.tag:'span');n.append(document.createTextNode(value.text||''));
  const styles=['color','backgroundColor','fontSize','fontWeight','fontStyle','textDecoration','textAlign','fontFamily'];for(const [key,v] of Object.entries(value.style||{}))if(styles.includes(key))n.style[key]=v;
  for(const child of value.children||[]){n.append(flowNode(child.node));if(child.tail)n.append(document.createTextNode(child.tail));}return n;
}
function renderDocument(data,body){
  $('inspector-type').textContent='FILE VIEW';$('inspector-note').textContent='Layout reconstructed from this .mcdx: stored positions, text, diagrams and equations. '+(data.has_cached_results?'Visible values are saved results in this file, not recalculated.':'This file has no saved results. The Results tab shows the separate CalcpadCE calculation when available.');
  $('preview-summary').textContent=data.has_cached_results?'Saved values from this file':'Equations awaiting Mathcad calculation';
  body.classList.add('worksheet-view');
  const page=data.page,toolbar=el('div',undefined,'document-toolbar'),shell=el('div',undefined,'document-shell'),canvas=el('div',undefined,'document-page'),pageSelect=el('select'),label=el('label','Page '),details=el('details',undefined,'document-region-details');
  pageSelect.setAttribute('aria-label','Worksheet page');for(let i=1;i<=page.count;i++){const option=el('option',i+' / '+page.count);option.value=i;pageSelect.append(option);}label.append(pageSelect);
  let number=1,query='';const matches=el('span',undefined,'document-matches');const previous=button('←',()=>show(number-1)),next=button('→',()=>show(number+1)),last=button('Last page',()=>show(page.count));previous.setAttribute('aria-label','Previous page');next.setAttribute('aria-label','Next page');
  const desk=el('div',undefined,'document-desk'),rail=el('nav',undefined,'page-rail'),stage=el('div',undefined,'document-stage');rail.setAttribute('aria-label','Worksheet pages');desk.append(rail,stage);stage.append(shell,details);
  const pagesToggle=button('Pages',()=>{rail.hidden=!rail.hidden;pagesToggle.setAttribute('aria-pressed',String(!rail.hidden));resize();});const compact=window.matchMedia('(max-width:560px)');const adaptPages=()=>{rail.hidden=compact.matches;pagesToggle.setAttribute('aria-pressed',String(!rail.hidden));};adaptPages();compact.addEventListener('change',adaptPages);
  const zoom=el('select');zoom.setAttribute('aria-label','Page zoom');for(const [value,text]of [['page','Fit page'],['width','Fit width'],['1','100%']]){const option=el('option',text);option.value=value;zoom.append(option);}if(compact.matches)zoom.value='width';zoom.addEventListener('change',()=>resize());toolbar.append(pagesToggle,previous,label,next,last,zoom,matches);body.append(toolbar,desk);shell.append(canvas);
  const detailsTitle=el('summary','Select a region to inspect its source'),detailsBody=el('pre');details.append(detailsTitle,detailsBody);
  function regionNode(r,top,offset,interactive=true){const n=el('div',undefined,'file-region '+r.kind);n.style.left=(page.margins[0]+Number(r.left))+'px';n.style.top=(offset+top)+'px';n.style.width=Number(r.width)+'px';n.style.minHeight=Number(r.height)+'px';for(const [key,v]of Object.entries(r.style||{}))n.style[key]=v;n.dataset.region=r.id;n.title=r.kind==='math'?r.expression:r.text;
    if(r.kind==='math'){const math=document.createElementNS('http://www.w3.org/1998/Math/MathML','math');math.append(mathNode(r.presentation));n.append(math);}else{if(r.image){const img=el('img');img.src=r.image;img.alt='Diagram from worksheet';img.style.width='100%';img.style.height=Number(r.height)+'px';n.append(img);}for(const flow of r.flows||[])n.append(flowNode(flow));if(!r.image&&!(r.flows||[]).length)n.textContent=r.text;}
    if(query&&(r.expression||r.text||'').toLowerCase().includes(query))n.classList.add('search-match');
    if(interactive)n.addEventListener('click',()=>{detailsTitle.textContent='Region '+r.id+' · '+(r.kind==='math'?'Native expression':'File content');detailsBody.textContent=r.xml||r.text;details.open=true;});return n;
  }
  function populate(target,pageNumber,interactive){
    target.replaceChildren();target.style.width=page.width+'px';target.style.height=page.height+'px';
    for(const r of data.header)target.append(regionNode(r,Number(r.top),12,interactive));
    for(const r of data.regions){const top=Number(r.top),p=Math.floor(top/page.content_height)+1;if(p===pageNumber)target.append(regionNode(r,top-(pageNumber-1)*page.content_height,page.margins[1],interactive));}
    for(const r of data.footer)target.append(regionNode(r,Number(r.top),page.height-page.margins[3],interactive));
    target.append(el('span',pageNumber+' / '+page.count,'file-page-number'));
  }
  const thumbnails=[];
  const thumbnailObserver=new IntersectionObserver(entries=>{for(const entry of entries){if(entry.isIntersecting){const target=entry.target.firstElementChild;populate(target,Number(target.dataset.page),false);thumbnailObserver.unobserve(entry.target);}}},{root:rail,rootMargin:'160px'});
  for(let i=1;i<=page.count;i++){
    const thumb=button('',()=>show(i),'page-thumb'),mini=el('div',undefined,'thumbnail-sheet'),frame=el('div',undefined,'thumbnail-frame');
    thumb.setAttribute('aria-label','Go to page '+i);mini.setAttribute('aria-hidden','true');mini.dataset.page=i;
    const scale=68/page.width;mini.style.transform='scale('+scale+')';frame.style.width='68px';frame.style.height=(page.height*scale)+'px';frame.append(mini);thumb.append(frame,el('span',String(i).padStart(2,'0'),'thumbnail-number'));rail.append(thumb);thumbnails.push(thumb);thumbnailObserver.observe(frame);
  }
  function show(value){
    number=Math.max(1,Math.min(page.count,Number(value)));pageSelect.value=number;previous.disabled=number===1;next.disabled=number===page.count;
    populate(canvas,number,true);for(let i=0;i<thumbnails.length;i++)thumbnails[i].setAttribute('aria-current',String(i+1===number));
    const selected=thumbnails[number-1];if(selected.offsetTop<rail.scrollTop||selected.offsetTop+selected.offsetHeight>rail.scrollTop+rail.clientHeight)rail.scrollTop=selected.offsetTop-16;
    stage.scrollTop=0;resize();
  }
  function resize(){const width=stage.clientWidth-40;const scale=zoom.value==='1'?1:Math.max(.15,Math.min(width/page.width,zoom.value==='page'?(stage.clientHeight-48)/page.height:1.5));canvas.style.transform='scale('+scale+')';shell.style.width=(page.width*scale)+'px';shell.style.height=(page.height*scale)+'px';}
  pageSelect.addEventListener('change',()=>show(pageSelect.value));show(1);
  currentDocument={search(text){query=text;const found=data.regions.filter(r=>(r.expression||r.text||'').toLowerCase().includes(text));matches.textContent=text?found.length+' matches':'';if(text&&found.length)show(Math.floor(Number(found[0].top)/page.content_height)+1);else show(number);}};
  const observer=new ResizeObserver(()=>{if(body.contains(shell))resize();else {observer.disconnect();thumbnailObserver.disconnect();compact.removeEventListener('change',adaptPages);}});observer.observe(stage);
}
