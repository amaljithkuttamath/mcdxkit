'use strict';
const $=id=>document.getElementById(id);
const element=(tag,value,cls)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;if(cls)e.className=cls;return e;};
let connections=null,controlState=null,serviceReady=null;
let token='',project=null,busy=false,pdfBlob=null,pdfDocument=null,pdfPage=1,reviewDirty=false;
async function api(path,data){const options={headers:{'X-MCDXKit-Token':token}};if(data!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}const r=await fetch(path,options);const body=await r.json();if(!r.ok)throw new Error(body.error||'Request failed.');return body;}
function notify(message,error=false){$('notice').textContent=message;$('notice').className=error?'error':'';const opened=document.querySelector('dialog[open]');if(opened){let status=opened.querySelector('.dialog-notice');if(!status){status=element('p',undefined,'dialog-notice');status.setAttribute('role','status');opened.querySelector('.dialog-heading').after(status);}status.textContent=message;status.className='dialog-notice '+(error?'settings-error':'muted');}}
function openDialog(id){const dialog=$(id);dialog.querySelector('.dialog-notice')?.remove();dialog.showModal();}
$('open-connect').onclick=()=>openDialog('connect-dialog');
$('open-settings').onclick=()=>{openDialog('settings-dialog');run('Checking document services…',async()=>{await discoverServices();if(connections?.router_key_saved)await fetchModels();});};
for(const id of ['open-create','rail-create','empty-create'])$(id).onclick=()=>openDialog('create-dialog');
for(const button of document.querySelectorAll('.close-dialog'))button.onclick=()=>$(button.dataset.dialog).close();
$('pdfs').previousElementSibling.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('pdfs').click();}});

function lockControls(){if(!controlState)return;for(const e of document.querySelectorAll('button,input,select,textarea')){if(!controlState.has(e))controlState.set(e,e.disabled);e.disabled=true;}}
async function run(message,action){if(busy)return;busy=true;$('progress').hidden=false;notify(message);controlState=new Map();lockControls();try{await action();}catch(e){notify(e.message,true);}finally{busy=false;$('progress').hidden=true;controlState.forEach((value,e)=>{if(e.isConnected)e.disabled=value;});controlState=null;$('source-download').disabled=!pdfBlob;if($('build-package'))$('build-package').disabled=reviewDirty;updateReadiness();}}
function action(label,fn,cls='secondary'){const b=element('button',label,cls);b.type='button';b.addEventListener('click',fn);return b;}
function tab(name){for(const key of ['answers','draft','source','packages']){$('tab-'+key).classList.toggle('selected',key===name);}$((project?name:'answers')+'-view').scrollIntoView({block:'start',behavior:'instant'});}
for(const name of ['answers','draft','source','packages'])$('tab-'+name).onclick=()=>tab(name);
function configuration(c){
 connections=c;
 const search=c.embedding?.endsWith('@mcdxkit@OpenRouter')?c.embedding.slice(0,-'@mcdxkit@OpenRouter'.length):'';if(search&&!Array.from($('router-embedding').options).some(o=>o.value===search))$('router-embedding').append(new Option(search+' · saved',search));$('router-embedding').value=search;
 const selected=c.model||'';if(!Array.from($('router-model').options).some(o=>o.value===selected))$('router-model').append(new Option(selected+' · saved',selected));$('router-model').value=selected;
 $('service-label').textContent=serviceReady!==false&&c.ragflow_configured&&c.openrouter_configured?'Service settings':'Connect services';
 $('configuration').textContent=`RAGFlow: ${c.ragflow_configured?'configured':'needs setup'}. OpenRouter: ${c.openrouter_configured?'configured':'needs setup'}.`;
 for(const [id,key] of [['rag-url','url'],['embedding-model','embedding'],['rerank-model','reranker']])$(id).value=c[key]||'';
 for(const [id,key] of [['rag-key','rag_key_saved'],['router-key','router_key_saved']]){$(id).value='';$(id).placeholder=c[key]?'Leave blank to keep saved key':'Paste API key';$(id+'-status').textContent=c[key]?'· configured':'';}
 $('service-dot').classList.toggle('ready',serviceReady!==false&&c.ragflow_configured&&c.openrouter_configured);$('open-settings').title=serviceReady!==false&&c.ragflow_configured&&c.openrouter_configured?'Service connections configured':'Connect RAGFlow and OpenRouter';
}
async function fetchModels(){
  $('model-status').textContent='Checking key and loading models…';
  const selected=$('router-model').value,selectedSearch=$('router-embedding').value;
  try{
   const result=await api('/api/documents/models',{router_key:$('router-key').value.trim()});
   $('router-model').replaceChildren(new Option(result.models.length?'Choose a model':'No supported free models available',''));
   for(const model of result.models)$('router-model').append(new Option(model.name+' · '+model.id,model.id));
   if(result.models.some(m=>m.id===selected))$('router-model').value=selected;
   $('model-status').textContent=result.models.length?`${result.models.length} free text models available for this key. Choose one, then save settings. Availability and rate limits can change.`:'Key accepted, but this account has no compatible free text models. Check provider/privacy settings or refresh later.';
   const embeddings=result.embedding_models||[];
   $('router-embedding').replaceChildren(new Option(embeddings.length?'Choose a search model':'No supported free search models available',''));
   for(const model of embeddings)$('router-embedding').append(new Option(model.name+' · '+model.id,model.id));
   if(embeddings.some(m=>m.id===selectedSearch))$('router-embedding').value=selectedSearch;
   else if(!connections?.embedding&&embeddings.length)$('router-embedding').value=[...embeddings].sort((a,b)=>(b.context_length||0)-(a.context_length||0))[0].id;
   $('model-status').textContent+=` ${embeddings.length} free search models listed; connection is verified when indexing.`;
   notify('Model list updated.');
  }catch(error){$('model-status').textContent=error.message+' Model list was not updated. Retry with Refresh models.';throw error;}
}
async function loadModels(){await run('Loading available OpenRouter models…',fetchModels);}
async function discoverServices(){
 const searchSelection=$('router-embedding').value;
 const dirty={};for(const [id,key] of [['rag-url','url'],['rag-key','rag_key'],['embedding-model','embedding'],['rerank-model','reranker'],['router-key','router_key'],['router-model','model']]){const value=$(id).value;if(value&&(key.endsWith('key')||value!==(connections?.[key]||'')))dirty[id]=value;}
 const result=await api('/api/documents/discover',{});serviceReady=result.service.state==='ready';configuration(result.configuration);
 for(const [id,value] of Object.entries(dirty))$(id).value=value;
 if(searchSelection)$('router-embedding').value=searchSelection;
 $('start-service').hidden=!result.service.can_start;$('rag-service-title').textContent=result.service.title;$('rag-service-message').textContent=result.service.message;$('notice').textContent=result.service.title;document.querySelector('#settings-dialog .dialog-notice')?.remove();updateReadiness();
}
$('start-service').onclick=()=>run('Starting document search…',async()=>{await api('/api/documents/start-service',{});await discoverServices();notify('Document-service startup requested. Check again for its current status.');});
$('detect-services').onclick=()=>run('Checking document services…',discoverServices);
$('router-key').onchange=()=>loadModels();
$('refresh-models').onclick=()=>loadModels();
async function refreshList(){const data=await api('/api/documents');$('projects').replaceChildren(new Option('Choose a saved project',''));for(const p of data.projects)$('projects').append(new Option(p.title,p.id));if(project)$('projects').value=project.id;configuration(data.configuration);return data.projects;}
$('settings-form').onsubmit=e=>{e.preventDefault();run('Saving service settings…',async()=>{
 const settings={};for(const [id,key] of [['rag-url','url'],['rag-key','rag_key'],['embedding-model','embedding'],['rerank-model','reranker'],['router-key','router_key'],['router-model','model']])settings[key]=$(id).value.trim();
 const search=$('router-embedding').value;
 const saved=(search||!settings.embedding)?await api('/api/documents/connect-openrouter',{router_key:settings.router_key,model:settings.model,search_model:search}):await api('/api/documents/configure',settings);configuration(saved);await discoverServices();$('settings-result').textContent='Saved on this server. Connection is checked when you index sources or create a draft.';notify('Service settings saved.');
});};
function updateReadiness(){if(!project)return;const indexed=project.sources.length>0&&project.sources.every(s=>s.status==='ready'),configured=serviceReady!==false&&connections?.ragflow_configured&&connections?.openrouter_configured;$('ask').disabled=busy||!indexed||!configured;$('draft').disabled=busy||!indexed||!configured;$('ingest').disabled=busy||!project.sources.length||!connections?.ragflow_configured||serviceReady===false;$('question-hint').textContent=serviceReady===false?'Document search is not ready. Open Connect services for its status. You can inspect originals now.':!configured?'Connect services to index your documents and ask questions. You can inspect originals now.':!indexed?'Index your documents before asking a question.':'Answers cite your project sources. Verify the original passage before applying a value.';}
async function releasePDF(){const previous=pdfDocument;pdfDocument=null;pdfBlob=null;$('pdf-canvas').getContext('2d').clearRect(0,0,$('pdf-canvas').width,$('pdf-canvas').height);$('source-title').textContent='Choose a PDF from the file rail';$('pdf-pages').textContent='';$('source-download').disabled=true;if(previous)await previous.loadingTask.destroy();}
async function select(id){if(project?.id!==id)await releasePDF();project=await api('/api/documents/show',{project:id});history.replaceState(null,'','/documents?project='+id);render();await refreshList();if(!pdfDocument&&project.sources.length){const source=project.sources[0];await openPDF(source.id,source.name,1,false);}notify('Project restored. Originals, answers and packages are saved on this server.');}
function cite(e){return action(e.id+' · '+e.source_name,()=>run('Opening original source…',()=>openPDF(e.source_id,e.source_name,e.positions[0]?.[0])),'citation');}
async function renderPDFPage(){
 if(!pdfDocument)return;
 const page=await pdfDocument.getPage(pdfPage),canvas=$('pdf-canvas'),base=page.getViewport({scale:1});
 const width=Math.max(280,$('pdf-canvas-wrap').clientWidth-32),scale=Math.min(2,width/base.width),viewport=page.getViewport({scale:scale*(window.devicePixelRatio||1)});
 canvas.width=viewport.width;canvas.height=viewport.height;canvas.style.width=(viewport.width/(window.devicePixelRatio||1))+'px';canvas.style.height=(viewport.height/(window.devicePixelRatio||1))+'px';
 await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
 $('pdf-page').value=pdfPage;$('pdf-page').max=pdfDocument.numPages;$('pdf-pages').textContent='of '+pdfDocument.numPages;
 canvas.setAttribute('aria-label','Original PDF page '+pdfPage+' of '+pdfDocument.numPages);
}
async function openPDF(id,name,page,focus=true){
 const r=await fetch(`/api/documents/${project.id}/sources/${id}`,{headers:{'X-MCDXKit-Token':token}});if(!r.ok)throw new Error((await r.json()).error);
 const nextBlob=await r.blob();const pdfjs=await import('/pdfjs/pdf.mjs');pdfjs.GlobalWorkerOptions.workerSrc='/pdfjs/pdf.worker.mjs';
 await releasePDF();
 pdfDocument=await pdfjs.getDocument({data:new Uint8Array(await nextBlob.arrayBuffer()),standardFontDataUrl:'/pdfjs/standard_fonts/',cMapUrl:'/pdfjs/cmaps/',cMapPacked:true,wasmUrl:'/pdfjs/wasm/',isEvalSupported:false}).promise;
 pdfBlob=nextBlob;
 document.querySelectorAll('.source-row').forEach(row=>row.classList.toggle('current',row.dataset.sourceId===id));
 $('pdf-canvas-wrap').hidden=false;document.querySelector('.pdf-toolbar').hidden=false;pdfPage=Number.isInteger(page)&&page>0?Math.min(page,pdfDocument.numPages):1;$('source-title').textContent=name;$('source-download').disabled=false;if(focus)tab('source');await renderPDFPage();notify('Original PDF rendered with PDF.js. Verify the cited passage and its qualifications.');
}
$('previous-page').onclick=()=>run('Rendering page…',async()=>{if(pdfPage>1){pdfPage--;await renderPDFPage();}notify('Original source · page '+pdfPage);});
$('next-page').onclick=()=>run('Rendering page…',async()=>{if(pdfDocument&&pdfPage<pdfDocument.numPages){pdfPage++;await renderPDFPage();}notify('Original source · page '+pdfPage);});
$('pdf-page').onchange=()=>run('Rendering page…',async()=>{const value=Number($('pdf-page').value);if(!pdfDocument||!Number.isInteger(value)||value<1||value>pdfDocument.numPages)throw new Error('Choose a page in this PDF.');pdfPage=value;await renderPDFPage();notify('Original source · page '+pdfPage);});

$('pdf-page').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('pdf-page').onchange();}};

function save(blob,name){const u=URL.createObjectURL(blob),a=element('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),60000);}
$('source-download').onclick=()=>{if(pdfBlob)save(pdfBlob,$('source-title').textContent);};
async function artifact(run,name){const r=await fetch(`/api/documents/${project.id}/runs/${run.id}/${name}`,{headers:{'X-MCDXKit-Token':token}});if(!r.ok)throw new Error((await r.json()).error);return r;}
function render(){
 $('title').textContent=project.title;$('revision').textContent='Project revision '+project.revision;$('project-controls').hidden=false;$('question-composer').hidden=false;for(const view of ['answers','source','draft','packages'])$(view+'-view').hidden=false;$('pdf-canvas-wrap').hidden=!pdfDocument;document.querySelector('.pdf-toolbar').hidden=!pdfDocument;$('nav-documents').href='/documents?project='+project.id;$('nav-calculations').href='/?library='+project.id;$('calculation-workspace').href='/?library='+project.id;$('rail-empty').hidden=true;$('create-dialog').close();$('connect-dialog').close();$('project-context').textContent=project.report_sha256?'Library + calculation':'Document library';$('library-welcome').hidden=true;$('answer-workspace').hidden=false;const linked=Boolean(project.report_sha256);$('design-request').hidden=!linked;$('open-connect').hidden=linked;$('calculation-status').textContent=linked?`${project.report_name||'Load report'} · ${project.template_name||'Mathcad template'} · Cases ${project.options.cases.join(', ')}`:'Optional · connect when ready';const latest=project.drafts.at(-1);$('workflow-status').textContent=project.runs.length?'Package available':latest?.review?'Reviewed':latest?'Review required':project.sources.length?(project.sources.every(s=>s.status==='ready')?'Ready for questions':'Index project sources'):'Add project sources';
 const sources=$('sources');sources.replaceChildren();for(const s of project.sources){const row=element('div',undefined,'source-row');row.dataset.sourceId=s.id;row.append(action(s.name,()=>run('Opening source…',()=>openPDF(s.id,s.name))),element('small',s.status==='ready'?'Indexed':s.status==='uploaded'?'Saved · not indexed':s.status==='failed'?'Indexing failed · retry available':'Indexing'));if(s.status==='parsing'){const p=element('progress');p.max=1;if(s.progress>0)p.value=s.progress;p.setAttribute('aria-label','Indexing '+s.name);row.append(p);}sources.append(row);}
 filterSources();renderAnswers();renderDraft(project.drafts.at(-1));renderPackages();lockControls();
}
function filterSources(){const query=$('source-search').value.toLowerCase();let count=0;document.querySelectorAll('.source-row').forEach(row=>{row.hidden=!row.textContent.toLowerCase().includes(query);if(!row.hidden)count++;});$('source-count').textContent=`${count} of ${project.sources.length} documents · ${project.sources.filter(s=>s.status==='ready').length} indexed`;}
$('source-search').oninput=filterSources;
function renderAnswers(){
 const hasAnswers=Boolean(project.answers?.length);$('answer-workspace').hidden=!hasAnswers;
 const previous=$('answer-history').value,answers=project.answers||[];
 $('answer-history').replaceChildren();for(const answer of [...answers].reverse())$('answer-history').append(new Option(answer.request,answer.id));
 if(answers.some(a=>a.id===previous))$('answer-history').value=previous;
 showAnswer();
}
function showAnswer(){
 const paper=$('answer-content');paper.replaceChildren();const answer=(project.answers||[]).find(a=>a.id===$('answer-history').value);
 if(!answer){paper.append(element('h2','Ask a question about your project'),element('p','Add PDFs, index them, then ask a question above. Each answer links back to the original sources.'));return;}
 paper.append(element('h2',answer.request),element('p','AI answer · '+new Date(answer.created*1000).toLocaleString()+' · Verify the cited passages.','muted'));
 if(answer.evidence.some(e=>!project.sources.some(s=>s.sha256===e.source_sha256))||answer.revision!==project.revision)paper.append(element('p','Saved answer from an earlier project revision. Ask again to include later changes.','muted'));
 const evidence=new Map(answer.evidence.map(e=>[e.id,e]));
 for(const section of answer.content.sections){paper.append(element('h3',section.heading),element('p',section.body));for(const id of section.evidence_ids)paper.append(cite(evidence.get(id)));}
 if(answer.content.gaps.length){paper.append(element('h3','Missing information'));for(const gap of answer.content.gaps)paper.append(element('p',gap));}
 const details=element('details');details.append(element('summary','Inspect retrieved passages'));for(const e of answer.evidence){const block=element('section',undefined,'evidence');block.append(cite(e),element('pre',e.text));details.append(block);}paper.append(details);
}
$('answer-history').onchange=showAnswer;
function renderDraft(draft){
 reviewDirty=false;
 const paper=$('draft-content');paper.classList.remove('empty-paper');paper.replaceChildren();if(!project.report_sha256){paper.append(element('h2','Connect a calculation when you’re ready'),element('p','Your library works independently. Connect a raw report and Mathcad template to prepare a design basis and calculate a package.'),action('Connect calculation files',()=>openDialog('connect-dialog')));return;}if(!draft){paper.append(element('h2','Design basis'),element('p','Use your project sources to prepare a design basis for the connected calculation. Create a cited draft from the sidebar, then review its proposed inputs.'));return;}
 const stale=draft.revision!==project.revision;
 paper.append(element('h2','Design basis'),element('p',stale?'Sources changed. Create a new draft before reviewing or calculating.':draft.review?'Design-basis review recorded. You can generate the calculation package.':'Draft for review. Verify applicability and every proposed value.','muted'));
 const evidence=new Map(draft.evidence.map(e=>[e.id,e]));
 for(const section of draft.content.sections){paper.append(element('h3',section.heading),element('p',section.body));for(const id of section.evidence_ids)paper.append(cite(evidence.get(id)));}
 paper.append(element('h2','Proposed template inputs'));
 if(!draft.content.suggestions.length)paper.append(element('p','No input changes proposed. Existing template inputs will be retained.'));
 const boxes=[];
 draft.content.suggestions.forEach((s,index)=>{const row=element('div',undefined,'suggestion'),label=element('label'),box=element('input');box.type='checkbox';box.checked=draft.review?.accepted.includes(index)||false;box.disabled=stale;boxes.push(box);label.append(box,element('span',`${s.variable} = ${s.value} ${s.unit}`));row.append(label,element('p',s.reason));for(const id of s.evidence_ids)row.append(cite(evidence.get(id)));paper.append(row);});
 if(draft.content.gaps.length){paper.append(element('h2','Unresolved information'));for(const gap of draft.content.gaps)paper.append(element('p',gap));}
 const evidenceDetails=element('details');evidenceDetails.append(element('summary','Inspect retrieved passages'));
 for(const e of draft.evidence){const block=element('section',undefined,'evidence');block.append(cite(e),element('pre',e.text),element('small','SHA-256: '+e.source_sha256));evidenceDetails.append(block);}paper.append(evidenceDetails);
 if(stale)return;
 const form=element('form',undefined,'review-form'),reviewer=element('input'),note=element('textarea');reviewer.id='reviewer';reviewer.required=true;reviewer.maxLength=160;reviewer.value=draft.review?.reviewer||'';note.id='review-note';note.required=true;note.maxLength=2000;note.value=draft.review?.note||'';
 const l1=element('label','Reviewed by');l1.htmlFor=reviewer.id;const l2=element('label','Review note / unresolved qualifications');l2.htmlFor=note.id;
 const submit=element('button','Save review');submit.type='submit';const build=action('Calculate & create package',()=>run('Calculating and assembling the package…',async()=>{await api('/api/documents/build',{project:project.id,draft:draft.id});await select(project.id);tab('packages');notify('Package created from the calculation engine. Native Prime execution remains unverified.');}));
 form.append(l1,reviewer,l2,note,element('p','Only checked inputs are applied. Unchecked inputs remain as defined in the template. This review is not engineering certification.','muted'),submit);
 form.onsubmit=e=>{e.preventDefault();run('Saving review…',async()=>{await api('/api/documents/review',{project:project.id,draft:draft.id,accepted:boxes.flatMap((b,i)=>b.checked?[i]:[]),reviewer:reviewer.value,note:note.value});await select(project.id);notify('Review saved. Generate the package when ready.');});};
 // Editing a review requires saving before another build; the server always uses the saved review.
 if(draft.review){build.id='build-package';form.append(build);for(const input of [...boxes,reviewer,note])input.addEventListener('input',()=>{reviewDirty=true;build.disabled=true;notify('Review changed. Save it before calculating.');});}
 paper.append(form);
}
function renderPackages(){const parent=$('packages-view');parent.replaceChildren();if(!project.runs.length)parent.append(element('div','Review a draft, then calculate to create a package.','paper'));for(const result of [...project.runs].reverse()){const row=element('article',undefined,'paper');row.append(element('h2','Calculation package'),element('p',new Date(result.created*1000).toLocaleString(),'muted'),element('p',result.check_summary?.total?`${result.check_summary.passed} checks passed · ${result.check_summary.failed} failed`:'Calculated · No checks found'),element('p','Calcpad calculation completed. Prime-native execution unverified.','muted'));const buttons=element('div',undefined,'actions');buttons.append(action('Download package ZIP',()=>run('Preparing download…',async()=>{save(await (await artifact(result,'package.zip')).blob(),'design-package.zip');notify('Package download started. Originals, formulas, calculation and evidence are included.');})),action('Read report',()=>run('Opening report…',async()=>{const value=await (await artifact(result,'report.html')).text();const frame=element('iframe');frame.title='Generated design report';frame.className='report-frame';frame.setAttribute('sandbox','');frame.srcdoc=value;row.append(frame);notify('Generated report loaded. Download and extract the ZIP to follow its worksheet and original-source links.');})));row.append(buttons);parent.append(row);}}
$('projects').onchange=()=>{if($('projects').value)run('Loading project…',()=>select($('projects').value));};
$('create-form').onsubmit=e=>{e.preventDefault();run('Creating document library…',async()=>{const library=await api('/api/documents/create',{title:$('project-title').value});await select(library.id);tab('answers');notify('Library created. Add PDFs to begin.');});};
$('connect-form').onsubmit=e=>{e.preventDefault();run('Checking loads and template…',async()=>{async function upload(file,kind){const r=await fetch('/api/upload?'+new URLSearchParams({name:file.name,kind}),{method:'POST',headers:{'X-MCDXKit-Token':token,'Content-Type':'application/octet-stream'},body:file});const body=await r.json();if(!r.ok)throw new Error(body.error);return body;}const raw=await upload($('raw-file').files[0],'report');const template=await upload($('template-file').files[0],'template');const value=$('cases').value.trim(),map=$('input-map').value.trim();project=await api('/api/documents/connect',{project:project.id,id:raw.id,template_id:template.id,cases:value?value.split(/[\s,]+/).map(Number):null,input_map:map?JSON.parse(map):null,load_source:$('basis').value});await select(project.id);tab('draft');notify('Calculation files connected. Your PDFs and saved answers are preserved.');});};
$('pdfs').onchange=()=>run('Saving original PDFs…',async()=>{for(const file of $('pdfs').files){const r=await fetch('/api/documents/upload?'+new URLSearchParams({project:project.id,name:file.name}),{method:'POST',headers:{'X-MCDXKit-Token':token,'Content-Type':'application/octet-stream'},body:file});if(!r.ok)throw new Error((await r.json()).error);}await select(project.id);$('pdfs').value='';notify('Original PDFs saved. Index them to enable retrieval.');});
$('ingest').onclick=()=>run('Submitting sources to RAGFlow…',async()=>{project=await api('/api/documents/ingest',{project:project.id});render();notify('Indexing requested. Refresh status to see actual parser progress.');});
$('refresh-status').onclick=()=>run('Checking RAGFlow indexing status…',async()=>{project=await api('/api/documents/status',{project:project.id});render();notify(project.sources.every(s=>s.status==='ready')?'Sources indexed. Ready for questions.':'Some sources are not ready. Review their status.');});
$('draft').onclick=()=>run('Retrieving evidence and drafting with OpenRouter…',async()=>{await api('/api/documents/draft',{project:project.id,request:$('request').value});await select(project.id);tab('draft');notify('Draft created. Inspect citations and review the proposed inputs.');});
$('ask').onclick=()=>run('Retrieving evidence and answering with citations…',async()=>{const answer=await api('/api/documents/ask',{project:project.id,question:$('question').value});await select(project.id);$('answer-history').value=answer.id;showAnswer();tab('answers');notify('Answer saved. Follow citations to verify the original pages.');});
async function connect(){const r=await fetch('/api/session');if(!r.ok)throw new Error('Sign in through the calculation workspace, then reopen Document library.');token=(await r.json()).token;const projects=await refreshList();const pid=new URLSearchParams(location.search).get('project')||(projects.length===1?projects[0].id:null);if(pid)await select(pid);else{notify('Create a project to begin.');$('workflow-status').textContent='Create a library';}await discoverServices();}
run('Connecting…',connect);
