'use strict';
const $=id=>document.getElementById(id);
const element=(tag,value,cls)=>{const e=document.createElement(tag);if(value!==undefined)e.textContent=value;if(cls)e.className=cls;return e;};
let token='',project=null,busy=false,pdfBlob=null,pdfDocument=null,pdfPage=1,reviewDirty=false;
async function api(path,data){const options={headers:{'X-MCDXKit-Token':token}};if(data!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(data);}const r=await fetch(path,options);const body=await r.json();if(!r.ok)throw new Error(body.error||'Request failed.');return body;}
function notify(message,error=false){$('notice').textContent=message;$('notice').className=error?'error':'';}
async function run(message,action){if(busy)return;busy=true;$('progress').hidden=false;notify(message);const disabled=new Map([...document.querySelectorAll('button,input,select,textarea')].map(e=>[e,e.disabled]));disabled.forEach((_,e)=>e.disabled=true);try{await action();}catch(e){notify(e.message,true);}finally{busy=false;$('progress').hidden=true;disabled.forEach((value,e)=>{if(e.isConnected)e.disabled=value;});$('source-download').disabled=!pdfBlob;if($('build-package'))$('build-package').disabled=reviewDirty;}}
function action(label,fn,cls='secondary'){const b=element('button',label,cls);b.type='button';b.addEventListener('click',fn);return b;}
function tab(name){for(const key of ['draft','source','packages']){$(key+'-view').hidden=key!==name;$('tab-'+key).classList.toggle('selected',key===name);}}
for(const name of ['draft','source','packages'])$('tab-'+name).onclick=()=>tab(name);
function configuration(c){
 $('configuration').textContent=`RAGFlow: ${c.ragflow_configured?'configured':'needs setup'}. OpenRouter: ${c.openrouter_configured?'configured':'needs setup'}.`;
 for(const [id,key] of [['rag-url','url'],['embedding-model','embedding'],['rerank-model','reranker'],['router-model','model']])$(id).value=c[key]||'';
 for(const [id,key] of [['rag-key','rag_key_saved'],['router-key','router_key_saved']]){$(id).value='';$(id).placeholder=c[key]?'Leave blank to keep saved key':'Paste API key';$(id+'-status').textContent=c[key]?'· configured':'';}
 if(!c.ragflow_configured||!c.openrouter_configured)$('service-settings').open=true;
}
async function refreshList(){const data=await api('/api/documents');$('projects').replaceChildren(new Option('Choose a saved project',''));for(const p of data.projects)$('projects').append(new Option(p.title,p.id));if(project)$('projects').value=project.id;configuration(data.configuration);}
$('settings-form').onsubmit=e=>{e.preventDefault();run('Saving service settings…',async()=>{
 const settings={};for(const [id,key] of [['rag-url','url'],['rag-key','rag_key'],['embedding-model','embedding'],['rerank-model','reranker'],['router-key','router_key'],['router-model','model']])settings[key]=$(id).value.trim();
 const saved=await api('/api/documents/configure',settings);configuration(saved);$('settings-result').textContent='Saved on this server. Connection is checked when you index sources or create a draft.';notify('Service settings saved.');
});};
async function select(id){project=await api('/api/documents/show',{project:id});history.replaceState(null,'','/documents?project='+id);render();await refreshList();notify('Project restored. Originals, drafts and packages are saved on this server.');}
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
async function openPDF(id,name,page){
 const r=await fetch(`/api/documents/${project.id}/sources/${id}`,{headers:{'X-MCDXKit-Token':token}});if(!r.ok)throw new Error((await r.json()).error);
 pdfBlob=await r.blob();const pdfjs=await import('/pdfjs/pdf.mjs');pdfjs.GlobalWorkerOptions.workerSrc='/pdfjs/pdf.worker.mjs';
 if(pdfDocument)await pdfDocument.destroy();
 pdfDocument=await pdfjs.getDocument({data:new Uint8Array(await pdfBlob.arrayBuffer()),standardFontDataUrl:'/pdfjs/standard_fonts/',cMapUrl:'/pdfjs/cmaps/',cMapPacked:true,wasmUrl:'/pdfjs/wasm/',isEvalSupported:false}).promise;
 pdfPage=Number.isInteger(page)&&page>0?Math.min(page,pdfDocument.numPages):1;$('source-title').textContent=name;$('source-download').disabled=false;tab('source');await renderPDFPage();notify('Original PDF rendered with PDF.js. Verify the cited passage and its qualifications.');
}
$('previous-page').onclick=()=>run('Rendering page…',async()=>{if(pdfPage>1){pdfPage--;await renderPDFPage();}notify('Original source · page '+pdfPage);});
$('next-page').onclick=()=>run('Rendering page…',async()=>{if(pdfDocument&&pdfPage<pdfDocument.numPages){pdfPage++;await renderPDFPage();}notify('Original source · page '+pdfPage);});
$('pdf-page').onchange=()=>run('Rendering page…',async()=>{const value=Number($('pdf-page').value);if(!pdfDocument||!Number.isInteger(value)||value<1||value>pdfDocument.numPages)throw new Error('Choose a page in this PDF.');pdfPage=value;await renderPDFPage();notify('Original source · page '+pdfPage);});

function save(blob,name){const u=URL.createObjectURL(blob),a=element('a');a.href=u;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),60000);}
$('source-download').onclick=()=>{if(pdfBlob)save(pdfBlob,$('source-title').textContent);};
async function artifact(run,name){const r=await fetch(`/api/documents/${project.id}/runs/${run.id}/${name}`,{headers:{'X-MCDXKit-Token':token}});if(!r.ok)throw new Error((await r.json()).error);return r;}
function render(){
 $('title').textContent=project.title;$('revision').textContent='Source revision '+project.revision;$('project-controls').hidden=false;$('new-project').open=false;
 const sources=$('sources');sources.replaceChildren();for(const s of project.sources){const row=element('div',undefined,'source-row');row.append(action(s.name,()=>run('Opening source…',()=>openPDF(s.id,s.name))),element('small',s.status==='ready'?'Indexed':s.status==='uploaded'?'Saved · not indexed':s.status==='failed'?'Indexing failed · retry available':'Indexing'));if(s.status==='parsing'){const p=element('progress');p.max=1;if(s.progress>0)p.value=s.progress;p.setAttribute('aria-label','Indexing '+s.name);row.append(p);}sources.append(row);}
 renderDraft(project.drafts.at(-1));renderPackages();
}
function renderDraft(draft){
 reviewDirty=false;
 const paper=$('draft-content');paper.replaceChildren();if(!draft){paper.append(element('h2','Design basis'),element('p','Add and index your project PDFs, then create a cited draft. Suggestions are never applied until you review them.'));return;}
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
function renderPackages(){const parent=$('packages-view');parent.replaceChildren();if(!project.runs.length)parent.append(element('div','Review a draft, then calculate to create a package.','paper'));for(const result of [...project.runs].reverse()){const row=element('article',undefined,'paper');row.append(element('h2',new Date(result.created*1000).toLocaleString()),element('p','Calcpad calculation completed · Prime-native execution unverified'),element('p','Check summary: '+JSON.stringify(result.check_summary)));const buttons=element('div',undefined,'actions');buttons.append(action('Download package ZIP',()=>run('Preparing download…',async()=>save(await (await artifact(result,'package.zip')).blob(),'design-package.zip'))),action('Read report',()=>run('Opening report…',async()=>{const value=await (await artifact(result,'report.html')).text();const frame=element('iframe');frame.title='Generated design report';frame.className='report-frame';frame.setAttribute('sandbox','');frame.srcdoc=value;row.append(frame);notify('Generated report loaded. Download and extract the ZIP to follow its worksheet and original-source links.');})));row.append(buttons);parent.append(row);}}
$('projects').onchange=()=>{if($('projects').value)run('Loading project…',()=>select($('projects').value));};
$('create-form').onsubmit=e=>{e.preventDefault();run('Checking loads and template…',async()=>{async function upload(file,kind){const r=await fetch('/api/upload?'+new URLSearchParams({name:file.name,kind}),{method:'POST',headers:{'X-MCDXKit-Token':token,'Content-Type':'application/octet-stream'},body:file});const body=await r.json();if(!r.ok)throw new Error(body.error);return body;}const raw=await upload($('raw-file').files[0],'report');const template=await upload($('template-file').files[0],'template');const value=$('cases').value.trim(),map=$('input-map').value.trim();project=await api('/api/documents/create',{id:raw.id,template_id:template.id,title:$('project-title').value,cases:value?value.split(/[\s,]+/).map(Number):null,input_map:map?JSON.parse(map):null,load_source:$('basis').value});await select(project.id);});};
$('pdfs').onchange=()=>run('Saving original PDFs…',async()=>{for(const file of $('pdfs').files){const r=await fetch('/api/documents/upload?'+new URLSearchParams({project:project.id,name:file.name}),{method:'POST',headers:{'X-MCDXKit-Token':token,'Content-Type':'application/octet-stream'},body:file});if(!r.ok)throw new Error((await r.json()).error);}await select(project.id);$('pdfs').value='';notify('Original PDFs saved. Index them to enable retrieval.');});
$('ingest').onclick=()=>run('Submitting sources to RAGFlow…',async()=>{project=await api('/api/documents/ingest',{project:project.id});render();notify('Indexing requested. Refresh status to see actual parser progress.');});
$('refresh-status').onclick=()=>run('Checking RAGFlow indexing status…',async()=>{project=await api('/api/documents/status',{project:project.id});render();notify(project.sources.every(s=>s.status==='ready')?'Sources indexed. Ready to draft.':'Some sources are not ready. Review their status.');});
$('draft').onclick=()=>run('Retrieving evidence and drafting with OpenRouter…',async()=>{await api('/api/documents/draft',{project:project.id,request:$('request').value});await select(project.id);tab('draft');notify('Draft created. Inspect citations and review the proposed inputs.');});
async function connect(){const r=await fetch('/api/session');if(!r.ok)throw new Error('Sign in through the calculation workspace, then reopen Design packages.');token=(await r.json()).token;await refreshList();const pid=new URLSearchParams(location.search).get('project');if(pid)await select(pid);else{notify('Create a project to begin.');$('new-project').open=true;}}
run('Connecting…',connect);
