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
// Shared labels and number formatting for loads.
const componentNames={P:'Axial load',Vy:'Shear y',Vz:'Shear z',My:'Moment y',Mz:'Moment z',V:'Lateral resultant'};
const fmt=value=>Number(Number(value).toPrecision(6)).toString(),unitOf=key=>key.startsWith('M')?'kip-in':'kip',plural=(n,word)=>`${n} ${word}${n===1?'':'s'}`,listed=a=>a.length<2?a.join(''):a.slice(0,-1).join(', ')+' and '+a[a.length-1];
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
// Decisions are sent only when the engineer made one, so outputs are otherwise unchanged by the checks.
function options(item,decisions=[]){return {id:item.id,cases:item.selected,load_source:$('basis').value,template_id:templateId,overrides:overrides(),...mappingOptions(),...(decisions.length?{review_decisions:decisions}:{})};}
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
  const reviews=new Map(items.map(i=>[i,reviewGroups(i)]));{const toReview=items.reduce((n,i)=>n+openReviews(reviews.get(i)),0);$('queue-summary').textContent=ready().length+' ready'+(toReview?` · ${toReview} to review`:'');}
  $('download-summary').hidden=items.filter(i=>i.inspection).length<2;$('download-summary').disabled=selectedReports().length<2;
  $('review-next').disabled=!items.length;$('compare-next').disabled=!hasTemplate||!ready().length;
  $('convert-all').disabled=!items.some(i=>i.preview&&!i.result);
  $('inspect-template').disabled=!hasTemplate;$('choose-template').textContent=hasTemplate?'Change template':'Choose template';$('inspect-template-review').disabled=!hasTemplate;
  const imports=$('import-list');imports.replaceChildren();
  for(const item of items){const row=el('div',undefined,'import-row');row.append(el('span',item.kind==='report'?'REPORT':'MCDX'),el('b',item.name),el('span',item.error?'Needs attention':'Added','tag'+(item.error?' error':'')),removeButton(item));imports.append(row);}
  const queue=$('queue');queue.replaceChildren();if(!items.length)queue.append(el('div','Add a report to check its inputs.','empty'));
  for(const item of items){const card=el('article',undefined,'card'),heading=head(item,item.inspection?'Local load envelope':item.error?'Needs attention':'Worksheet');if(item.kind==='report'&&hasTemplate&&item.selected.length)heading.append(button(libraryProject?.report_sha256?'View project design basis':libraryProject?'Connect to project library':'Create design package',()=>run(async()=>{if(libraryProject?.report_sha256){location.href='/documents?project='+libraryProject.id;return;}const settings=options(item);if(Object.keys(settings.overrides||{}).length)throw new Error('Save overrides in a new template, or review document-backed suggestions before connecting.');const project=await api(libraryProject?'/api/documents/connect':'/api/documents/create',{...settings,...(libraryProject?{project:libraryProject.id}:{title:item.name.replace(/\.[^.]+$/,'')})});location.href='/documents?project='+project.id;})));card.append(heading);
    if(item.inspection){card.append(pairValidation(item.inspection));const groups=reviews.get(item),open=groups.filter(g=>!g.record),flagged=new Map(groups.map(g=>[g.case,g])),governing=item.inspection.governing;const caseDetails=el('details',undefined,'load-cases');caseDetails.open=item.casesOpen??(open.length>0||Boolean(item.inspection.selection_required));caseDetails.addEventListener('toggle',()=>{item.casesOpen=caseDetails.open;});caseDetails.append(el('summary',`${item.selected.length} of ${item.inspection.cases.length} load cases selected`),el('p',item.inspection.summary_only?'Summary-only report: case names are absent. Choose the load cases to include.':item.inspection.selection_required?'Choose the load cases to include.':'STR cases selected by default.','case-caption'));const cases=el('div',undefined,'cases');for(const c of item.inspection.cases){const label=el('label',undefined,'case-label'),input=el('input'),text=el('span',undefined,'case-text'),group=flagged.get(c.id),governs=governing&&item.selected.includes(c.id)?Object.keys(governing).filter(k=>governing[k]?.case===c.id):null;input.type='checkbox';input.checked=item.selected.includes(c.id);input.addEventListener('change',()=>run(async()=>{item.selected=input.checked?[...item.selected,c.id].sort((a,b)=>a-b):item.selected.filter(id=>id!==c.id);await refresh(item);}));text.append(el('span',`${c.id}: ${c.name||'Unnamed'}`));if(governs)text.append(el('small',governs.length?'governs '+governs.join(', '):'governs no peak','case-governs'));label.append(input,text);if(group){input.setAttribute('aria-describedby',reviewId(item,c.id));if(!group.record){label.classList.add('flagged');label.append(el('span','! Review','review-tag'));}}cases.append(label);}caseDetails.append(cases);card.append(caseDetails);
      {const review=reviewView(item,groups);if(review)card.append(review);}
      if(item.inspection.envelope){const values=el('div',undefined,'envelope');if(open.length)values.append(el('p',`${plural(open.length,'item')} to review above`,'load-caption'));for(const [key,value]of Object.entries(item.inspection.envelope)){const cell=el('div',undefined,'measure');const label=el('span',undefined,'measure-label');const gov=item.inspection.governing?.[key];label.append(el('span',componentNames[key]||key),el('small',gov?`${key} · case ${gov.case}${gov.case_name?' '+gov.case_name:''}${gov.lead_ratio&&gov.lead_ratio>=1.5?` · ${gov.lead_ratio.toFixed(1)}× next case`:''}`:key));cell.append(label,el('strong',fmt(value)),el('small',unitOf(key)));values.append(cell);}card.append(values);}
      {const note=dominanceView(item.inspection.review_checks,item.inspection.cases);if(note)card.append(note);}
    }
    if(item.advice)card.append(adviceView(item));
    if(item.error)card.append(el('p',item.error,'error-text'));
    if(item.validation)validation(card,item.validation);
    if(item.id){const actions=el('div',undefined,'card-actions');actions.append(button(item.kind==='report'?'Open report':'Open file',()=>run(()=>inspect(item.id))));if(item.kind==='report'&&item.inspection)actions.append(button('Suggest cases',()=>run(async()=>{const fresh=await api('/api/inspect',{id:item.id,cases:item.selected.length?item.selected:undefined,load_source:$('basis').value});if(!fresh.case_suggestions){notice('Case suggestions are turned off on this server (MCDXKIT_CHECKS).',true);return;}item.advice=fresh.case_suggestions;notice('Review the suggested cases before using them.');})));if(item.validation)actions.append(button('Validate again',()=>run(async()=>{item.validation=await api('/api/validate',{id:item.id});notice('Package checks passed. Native execution requires Mathcad.');})));;card.append(actions);}queue.append(card);
  }
  const comparisons=$('comparisons');comparisons.replaceChildren();let count=0;
  for(const item of items){if(!item.preview&&!item.previewError)continue;count++;const card=el('article',undefined,'card');card.append(head(item,item.preview?'Ready to save':'Needs attention'));if(item.preview){const changes=item.preview.diff.changes;card.append(el('p',`${changes.filter(c=>c.kind!=='added').length} updated expressions and ${changes.filter(c=>c.kind==='added').length} added expressions`,'small'));{const pending=pendingView(item);if(pending)card.append(pending);}const actions=el('div',undefined,'card-actions');actions.append(button('View changes',()=>run(()=>inspectDiff(item.preview.worksheet.id))),button('Open proposed file',()=>run(()=>inspect(item.preview.worksheet.id))));card.append(actions);}else card.append(el('p',item.previewError,'error-text'));comparisons.append(card);}if(!count)comparisons.append(el('div','Check your inputs first, then select Review changes.','empty'));
  const results=$('results');results.replaceChildren();for(const item of items.filter(i=>i.previewError)){const failed=el('article',undefined,'card');failed.append(head(item,'Generation needs attention'),el('p',item.previewError,'error-text'));results.append(failed);}for(const item of items.filter(i=>i.result))results.append(resultCard(item.result,item.name));if(!items.some(i=>i.result))results.append(el('div','Your files will appear here. Earlier conversions are in Saved outputs.','empty'));
  if(focusNext&&!busy){const target=$(focusNext);focusNext=null;if(target&&!target.disabled)target.focus();}
}
function resultCard(result,name){
  const card=el('article',undefined,'card output-card');card.append(head({name:result.worksheet.name||name},result.calculation?.calculated?'Calculated files ready':'Files ready'));{const chip=reviewChip(result.review_checks);if(chip)card.append(chip);}
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
// Review checks are advisory: nothing changes until the engineer chooses.
// A decision is stored per flag (report content, load source, check, case, component) and resolves the whole case card.
const checkNames={service_axial_above_strength:'Service cases against strength cases',effects_below_top:'Effects along the pile against pile-top reactions',duplicate_case:'Copied cases',ratio_outlier:'Unusual load ratios within a case',gross_magnitude:'Possible unit slips'};
const caseLabel=c=>c===null?'The report':'Case '+c,caseText=c=>c===null?'the report':'case '+c;
const decisionText={included_case:c=>`${caseText(c)} added to the envelope.`,kept_service:c=>`${caseText(c)} kept as is.`,will_fix_in_group:c=>`${caseText(c)} kept; you will fix the report in GROUP and upload it again.`};
const reviewMemory=new Map(),reviewStore='mcdxkit.review-decisions';let focusNext=null;
// Stored rows are checked before use: a malformed or older row is dropped, never rendered or sent.
const storedDecision=d=>Boolean(d)&&typeof d==='object'&&typeof d.scope==='string'&&typeof d.rule==='string'&&(d.case===null||Number.isInteger(d.case))&&(d.component===null||typeof d.component==='string')&&Object.hasOwn(decisionText,d.decision)&&(d.note===null||typeof d.note==='string')&&typeof d.decided_at==='string';
try{for(const [key,value] of Object.entries(JSON.parse(localStorage.getItem(reviewStore)||'{}')))if(storedDecision(value))reviewMemory.set(key,value);}catch{/* Storage is optional; decisions then last for this page. */}
function saveDecisions(){try{const kept={};for(const [key,{added_by,...row}] of reviewMemory)kept[key]=row;localStorage.setItem(reviewStore,JSON.stringify(kept));}catch{/* Storage is optional. */}}
function reviewScope(item){return (item.sha||item.id)+'|'+(item.inspection?.load_source||$('basis').value);}
function flagKey(item,f){return [reviewScope(item),f.rule,f.case,f.component].join('|');}
function cardWorthy(f){return Boolean(f.impact?.envelope||f.impact?.selection);}
// An "added" decision only stands while the case is still selected.
function standing(item,d){return d&&(d.decision!=='included_case'||Boolean(item.selected?.includes(d.case)));}
function caseRecords(item,c){const scope=reviewScope(item);return [...reviewMemory.values()].filter(d=>d.scope===scope&&d.case===c&&standing(item,d));}
// Decisions sent to the engine: only for flags that the given review actually raised.
function reviewDecisions(item,flags){const raised=new Set((flags||[]).map(f=>flagKey(item,f)));return [...raised].map(key=>reviewMemory.get(key)).filter(d=>standing(item,d)).map(({rule,case:c,component,decision,note,decided_at})=>({rule,case:c,component,decision,note,decided_at}));}
function decided(item,f){return standing(item,reviewMemory.get(flagKey(item,f)));}
// One card per case: undecided cards hold the flags that could change the envelope or selection.
// A case counts as reviewed only when every such flag has a decision; a flag that becomes card-worthy
// later (for example a unit slip once the case is added) reopens the card with just that flag.
function reviewGroups(item,review=item.inspection?.review_checks){
  const flags=review?.flags||[],cases=new Map(),magnitude=g=>Math.max(0,...g.flags.map(f=>f.impact?.magnitude||0)),scope=reviewScope(item);
  for(const f of flags.filter(cardWorthy))if(!cases.has(f.case))cases.set(f.case,null);
  for(const d of reviewMemory.values())if(review&&d.scope===scope&&standing(item,d)&&!cases.has(d.case))cases.set(d.case,null);
  return [...cases.keys()].map(c=>{const mine=flags.filter(f=>f.case===c).sort((a,b)=>(b.impact?.magnitude||0)-(a.impact?.magnitude||0)),undecided=mine.filter(f=>cardWorthy(f)&&!decided(item,f)),record=undecided.length?null:caseRecords(item,c)[0]||null;return {case:c,record,flags:record?mine:undecided};}).filter(g=>g.record||g.flags.length).sort((a,b)=>magnitude(b)-magnitude(a)||(a.case??0)-(b.case??0));
}
// Card-worthy flags of a generated review that carry no verified decision, by case (largest impact first).
function undecidedByCase(review){
  const key=f=>[f.rule,f.case,f.component].join('|'),made=new Set((review?.decisions||[]).filter(d=>!d.unverified).map(key)),open=new Map();
  for(const f of [...(review?.flags||[])].sort((a,b)=>(b.impact?.magnitude||0)-(a.impact?.magnitude||0)))if(cardWorthy(f)&&!made.has(key(f)))open.set(f.case,[...(open.get(f.case)||[]),f]);
  return {open,made,key};
}
function openReviews(groups){return groups.filter(g=>!g.record).length;}
function reviewId(item,c){return `review-${item.id}-${c}`;}
function caseName(cases,id){const c=cases?.find(x=>x.id===id);return `case ${id}${c?.name?' '+c.name:''}`;}
function flagTitle(f){const c=caseLabel(f.case);return ({service_axial_above_strength:`${c} has more axial load than any strength case`,effects_below_top:`${c} has a smaller ${(componentNames[f.component]||'value').toLowerCase()} along the pile than at the pile top`,duplicate_case:`${c} has the same tables as case ${f.evidence?.duplicate_of}`,ratio_outlier:`${c} has an unusual load ratio`,gross_magnitude:`${c} may use different units`})[f.rule]||`${c} needs a look`;}
function flagNumbers(f,cases){return f.rule==='service_axial_above_strength'?`Axial load ${fmt(f.value)} ${f.unit} in ${caseName(cases,f.case)}. Largest strength case: ${fmt(f.compared_to)} ${f.unit} in ${caseName(cases,f.evidence?.strength_case)}.`:f.message;}
function impactText(item,f){
  const i=f.impact,env=item.inspection?.envelope||{},parts=i.components||[];
  if(f.case!==null&&!item.selected.includes(f.case)){
    if(f.rule==='service_axial_above_strength'&&env.P>0&&f.value>env.P){const others=parts.filter(k=>k!=='P');return `If you add it, P rises ${(100*(f.value-env.P)/env.P).toFixed(1)}%`+(others.length?`, and ${listed(others)} rise${others.length===1?'s':''} too.`:'.');}
    return parts.length?`If you add it, ${listed(parts)} rise${parts.length===1?'s':''}.`:'Adding it would change the case selection.';
  }
  if(i.envelope)return parts.length?`It sets the ${listed(parts)} peak${parts.length===1?'':'s'}, so a wrong value here changes the envelope.`:'It is in the envelope, so a wrong value here could change it.';
  return 'It affects which cases belong in the envelope.';
}
// A decision changes what the next preview sends, so every preview of the same report content is dropped.
function dropPreviews(scope){for(const other of items)if(other.inspection&&reviewScope(other)===scope)other.preview=null;}
// Records the decision for the flags this card showed. An existing decision is never overwritten; Undo first.
function decide(item,group,decision,addedBy){
  if(!group.flags.length||group.flags.some(f=>decided(item,f)))return false;
  const at=new Date().toISOString(),note=decision==='included_case'?null:(item.keepNote||'').trim().slice(0,500)||null;
  for(const f of group.flags)reviewMemory.set(flagKey(item,f),{scope:reviewScope(item),rule:f.rule,case:f.case,component:f.component,decision,note,decided_at:at,...(addedBy?{added_by:addedBy}:{})});
  saveDecisions();item.keeping=undefined;item.keepNote='';item.preview=null;dropPreviews(reviewScope(item));focusNext=reviewId(item,group.case)+'-undo';return true;
}
async function includeCase(item,group){
  const c=group.case,f=group.flags[0];item.selected=[...item.selected,c].sort((a,b)=>a-b);await refresh(item);
  if(item.error||!item.inspection.envelope){const problem=item.error;item.selected=item.selected.filter(x=>x!==c);await refresh(item);throw new Error(`Case ${c} was not added: ${problem||'no envelope'}`);}
  decide(item,group,'included_case',item.id);
  const key=item.inspection.envelope[f.component]===undefined?'P':f.component,gov=item.inspection.governing?.[key];
  notice(`${componentNames[key]} now ${fmt(item.inspection.envelope[key])} ${unitOf(key)}`+(gov?.case===c?`, from case ${c}`:'')+'.');
}
// Undo removes only this case's decision and, if this page added the case, only that case from the selection.
async function undoDecision(item,group){
  const c=group.case,scope=reviewScope(item),records=[...reviewMemory].filter(([,d])=>d.scope===scope&&d.case===c),added=records.some(([,d])=>d.decision==='included_case'&&d.added_by===item.id);
  for(const [key] of records)reviewMemory.delete(key);saveDecisions();item.preview=null;dropPreviews(scope);focusNext=reviewId(item,c)+'-show';
  if(added&&item.selected.includes(c)&&item.selected.length>1){const key=group.flags[0]?.component&&item.inspection.envelope?.[group.flags[0].component]!==undefined?group.flags[0].component:'P';item.selected=item.selected.filter(x=>x!==c);await refresh(item);notice(`Undone. ${componentNames[key]} back to ${fmt(item.inspection.envelope?.[key])} ${unitOf(key)}; case ${c} is back to review.`);}
  else notice(`Undone. ${caseLabel(c)} is back to review.`);
}
function reviewCard(item,group){
  const c=group.case,f=group.flags[0],id=reviewId(item,c),box=el('section',undefined,'review-card');box.id=id+'-card';box.setAttribute('aria-labelledby',id);
  if(group.record){const d=group.record,line=el('p',undefined,'review-done'),text=el('span','✓ Reviewed: '+decisionText[d.decision](c)+(d.note?' Note: '+d.note:''));text.id=id;const undo=button('Undo',()=>run(()=>undoDecision(item,group)),'review-undo');undo.id=id+'-undo';undo.setAttribute('aria-label',`Undo review of ${caseText(c)}`);line.append(text,undo);box.classList.add('reviewed');box.append(line);return box;}
  const title=el('h4',flagTitle(f),'review-title');title.id=id;box.append(title,el('p',flagNumbers(f,item.inspection.cases),'review-numbers'));
  for(const other of group.flags.slice(1))box.append(el('p',other.message,'review-numbers'));
  box.append(el('p',impactText(item,f),'review-impact'));
  const actions=el('div',undefined,'review-actions'),show=button('Show in report',()=>run(()=>inspect(item.id,'file',c)));show.id=id+'-show';actions.append(show);
  if(c!==null&&!item.selected.includes(c))actions.append(button(`Add case ${c} to envelope`,()=>run(()=>includeCase(item,group))));
  const keep=button('Keep as is…',()=>{item.keeping=item.keeping===c?undefined:c;item.keepNote='';focusNext=item.keeping===c?id+'-note':id+'-keep';render();});keep.id=id+'-keep';keep.setAttribute('aria-expanded',String(item.keeping===c));keep.setAttribute('aria-controls',id+'-why');actions.append(keep);box.append(actions);
  if(item.keeping===c){
    const why=el('div',undefined,'review-keep'),label=el('label','Note (optional)'),note=el('textarea');why.id=id+'-why';why.setAttribute('role','group');why.setAttribute('aria-label',`Why keep ${caseText(c)} as is`);note.id=id+'-note';label.htmlFor=note.id;note.rows=2;note.maxLength=500;note.value=item.keepNote||'';note.addEventListener('input',()=>{item.keepNote=note.value;});
    const choices=el('div',undefined,'review-actions'),choose=(decision,message)=>()=>{if(busy||!decide(item,group,decision))return;notice(message);render();};
    choices.append(button(f.rule==='service_axial_above_strength'&&c!==null?`Case ${c} really is a service case`:`${caseLabel(c)} is correct as is`,choose('kept_service',`Reviewed: ${caseText(c)} kept as is.`)),button('I’ll fix the report in GROUP and upload it again',choose('will_fix_in_group',`Reviewed: ${caseText(c)} kept until you fix the report in GROUP.`)),button('Cancel',()=>{item.keeping=undefined;focusNext=id+'-keep';render();},'quiet'));
    why.append(label,note,choices);box.append(why);
  }
  box.append(el('p','This asks you to look. It is not an error, and nothing changes unless you choose.','review-footer'));return box;
}
function checkedView(review){
  const box=el('details',undefined,'review-checked'),list=el('ul'),shown=new Set();box.append(el('summary','What was checked'),el('p','Automated consistency checks on the GROUP summary. They do not verify the design and never change inputs, case selection or results.','small'));
  const row=(id,state,extra=[])=>{if(shown.has(id))return;shown.add(id);const li=el('li');li.append(el('b',checkNames[id]||'Installed check '+(id||'')),el('span',state,'small'),...extra);list.append(li);};
  const skipped=id=>(review.skipped||[]).find(s=>s.id===id),error=id=>(review.errors||[]).find(e=>e.id===id);
  for(const check of (review.checks_run||[]).filter(c=>c.kind==='check')){
    if(check.status==='skipped'){row(check.id,'not applicable: '+(skipped(check.id)?.reason||'does not apply to this report'));continue;}
    if(check.status!=='ok'){row(check.id,'could not run: '+(error(check.id)?.message||check.status));continue;}
    const flags=(review.flags||[]).filter(f=>f.rule===check.id),minor=flags.filter(f=>!cardWorthy(f)),raised=flags.length-minor.length;
    row(check.id,!flags.length?'nothing found':[raised?`${raised} to review above`:'',minor.length?`${plural(minor.length,'item')} that cannot change the envelope`:''].filter(Boolean).join(', '),minor.map(f=>el('small',f.message,'review-minor')));
  }
  for(const s of review.skipped||[])if(!(review.checks_run||[]).some(c=>c.id===s.id&&c.kind!=='check'))row(s.id,'not applicable: '+s.reason);
  for(const e of review.errors||[])if(!(review.checks_run||[]).some(c=>c.id===e.id&&c.kind!=='check'))row(e.id,'could not run: '+e.message);
  box.append(list);return box;
}
function checkErrors(review){return review?.errors?.length?el('p','Input checks could not run; check the GROUP input yourself. '+review.errors.map(e=>`${checkNames[e.id]||e.id||'Review'}: ${e.message}`).join('; ')+'.','review-error-note'):null;}
function reviewView(item,groups){
  const review=item.inspection?.review_checks;if(!review||!(review.checks_run||[]).some(c=>c.kind==='check')&&!review.errors?.length)return null;
  const box=el('div',undefined,'review-checks');
  if(!groups.length)box.append(el('p',review.errors?.length?'Some input checks could not run. See what was checked.':'✓ Input checks found nothing to review','review-clean'));
  for(const g of groups.slice(0,3))box.append(reviewCard(item,g));
  if(groups.length>3){const more=el('details',undefined,'review-more');more.open=Boolean(item.reviewMore);more.addEventListener('toggle',()=>{item.reviewMore=more.open;});more.append(el('summary',`Show ${groups.length-3} more`));for(const g of groups.slice(3))more.append(reviewCard(item,g));box.append(more);}
  box.append(checkedView(review));return box;
}
function pendingView(item){
  // What the preview itself recorded, so Changes and Outputs agree even when a stale decision was dropped.
  const review=item.preview.review_checks,open=[...undecidedByCase(review).open.values()],errors=checkErrors(review);if(!open.length&&!errors)return null;const box=el('div',undefined,'review-pending');
  for(const [f] of open)box.append(el('p','Not reviewed yet: '+(f.rule==='service_axial_above_strength'&&!item.selected.includes(f.case)?`adding case ${f.case} would make axial load ${fmt(f.value)} ${f.unit}`:flagTitle(f).replace(/^Case/,'case').replace(/^The report/,'the report')),'review-pending-line'));
  if(open.length)box.append(el('p',`${plural(open.length,'item')} ${open.length===1?'isn’t':'aren’t'} reviewed yet. Outputs will list ${open.length===1?'it':'them'} as not reviewed.`,'small'));
  if(errors)box.append(errors);return box;
}
function reviewChip(review){
  if(!review)return null;const box=el('div',undefined,'review-summary'),{open,made,key}=undecidedByCase(review),flags=review.flags||[];
  // A case is reviewed only when none of its card-worthy flags is still undecided.
  const reviewed=new Set(flags.filter(f=>made.has(key(f))&&!open.has(f.case)).map(f=>f.case));
  if(reviewed.size||open.size)box.append(el('p','Input checks: '+[reviewed.size?`${reviewed.size} reviewed`:'',open.size?`${open.size} not reviewed`:''].filter(Boolean).join(' · '),'review-chip'));
  const errors=checkErrors(review);if(errors)box.append(errors);return reviewed.size||open.size||errors?box:null;
}
function caseLine(lines,id){let summary=-1;lines.forEach((line,i)=>{if(line.includes('SUMMARY FOR LOAD CASES AND COMBINATIONS'))summary=i;});const heading=new RegExp('LOAD CASE\\s*:\\s*'+id+'(?!\\d)');return lines.findIndex((line,i)=>i>summary&&heading.test(line));}
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
async function inspect(id,mode='file',caseId){currentViewFile=id;currentDocument=null;const found=items.find(i=>i.id===id||i.preview?.worksheet.id===id||i.result?.worksheet.id===id);if(found)activeItem=found;const data=await api('/api/view',{id});if(data.kind!=='report'&&id!==(templateId||'default-template'))currentWorksheetId=id;dialog(data.name,data.kind==='report'?'ORIGINAL SOURCE':'WORKSHEET INSPECTOR',data.kind==='report'?'Original report text. Search or scroll to inspect the source.':'Text and native equations extracted from the .mcdx. This is not a native Mathcad page rendering; no expressions are executed.');const body=$('inspector-content');markView(data.kind==='report'?'report':id===(templateId||'default-template')?'template':'worksheet');$('preview-summary').textContent=data.kind==='report'?'Original report text':'About this file';if(data.calculated_html)currentCalculatedId=id;$('view-calculation').disabled=!currentCalculatedId;if(data.calculated_html&&mode==='calculated'){markView('calculation');$('preview-summary').textContent='Calculated with CalcpadCE';$('inspector-title').textContent=data.name.replace(/\.mcdx$/i,'.cpd');$('inspector-type').textContent='CALCULATED WORKSHEET';$('inspector-note').textContent='Calculated locally with CalcpadCE. This page is a result snapshot; the .cpd and .mcdx files contain the formulas. Prime execution remains unverified.';const calculated=el('div',undefined,'calculated');appendCalculated(calculated,data.calculated_html);body.append(calculated);return;}if(data.kind==='report'){const lines=data.text.split('\n'),target=caseId===undefined||caseId===null?-1:caseLine(lines,caseId);const starts=[...new Set([...Array(Math.ceil(lines.length/100)).keys()].map(k=>k*100).concat(target>=0?[target]:[]))].sort((a,b)=>a-b);for(let k=0;k<starts.length;k++){const n=starts[k],block=el('pre',lines.slice(n,starts[k+1]??lines.length).map((line,i)=>String(n+i+1).padStart(5)+'  '+line).join('\n'),'source-text'+(n===target?' source-focus':''));block.dataset.search=block.textContent.toLowerCase();body.append(block);if(n===target){block.tabIndex=-1;block.setAttribute('aria-label',`Report text from load case ${caseId}, line ${n+1}`);}}const focus=body.querySelector('.source-focus');if(focus){$('preview-summary').textContent=`Load case ${caseId} in the final summary`;focus.scrollIntoView({block:'start'});focus.focus({preventScroll:true});}}else{renderDocument(data,body);return;}}
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
async function importFiles(files){let added=0,failed=0,skipped=0;for(const file of files){const ext=file.name.split('.').pop().toLowerCase();if(!['gp11t','txt','mcdx'].includes(ext)){skipped++;continue;}if(items.length>=100)throw new Error('The queue is limited to 100 files. Start a new session for more.');const item={name:file.name,relativePath:file.webkitRelativePath,kind:ext==='mcdx'?'worksheet':'report'};notice('Reading '+file.name+'…');try{if(file.size>(item.kind==='report'?16:32)*1024*1024)throw new Error('File exceeds the upload size limit.');Object.assign(item,await api('/api/upload?'+new URLSearchParams({name:file.name,kind:item.kind}),file,true));item.sha=item.source_sha256;if(item.inspection){item.selected=item.inspection.selected_cases;item.error=item.inspection.selection_required?(item.inspection.summary_only?'Select the load cases to include in Inputs.':item.inspection.selection_required):null;if(hasTemplate)await refresh(item);}added++;}catch(e){item.error=e.message;failed++;}items.push(item);activeItem=item;render();}if(activeItem?.id&&!compactView.matches)await inspect(activeItem.id);const toReview=items.reduce((n,i)=>n+openReviews(reviewGroups(i)),0);notice(`${added} file${added===1?'':'s'} added`+(failed?` · ${failed} failed`:'')+(skipped?` · ${skipped} unsupported files skipped`:'')+(toReview?` · ${plural(toReview,'item')} to review`:'')+'.',failed>0);}
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
      // Decisions go only for flags the latest inspection raised; the output repeats exactly what the preview used.
      let decisions = reviewDecisions(item, item.inspection?.review_checks?.flags);
      try { item.preview = await api('/api/preview', options(item, decisions)); }
      catch (error) {
        // A stale decision must never block outputs: retry without decisions; they then show as not reviewed.
        if (!decisions.length || !/review[ _]decision/i.test(error.message)) throw error;
        decisions = [];
        item.preview = await api('/api/preview', options(item));
        notice('A review decision no longer matches the checks for ' + item.name + '. Review its inputs again.');
      }
      item.previewDecisions = decisions;
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
    try { item.result = await api('/api/convert', options(item, item.previewDecisions || [])); }
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
