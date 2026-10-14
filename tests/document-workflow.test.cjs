// Real document UI orchestration with a small DOM boundary; layout is browser-tested.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(){
 const controls=[],nodes=new Map(),timers=new Map();let resolveFetch,nextTimer=0;
 function element(tag='div') {const e={tagName:tag,hidden:false,disabled:false,isConnected:true,value:'',dataset:{},children:[],attributes:{},events:{},classList:{add(){},remove(){},toggle(){}},setAttribute(key,value){this.attributes[key]=value;},addEventListener(name,fn){this.events[name]=fn;},append(...kids){this.children.push(...kids);},replaceChildren(...kids){this.children=kids;},querySelector(){return null;},close(){},scrollIntoView(){}};if(['button','input','select','textarea'].includes(tag))controls.push(e);return e;}
 const html=fs.readFileSync('src/mcdxkit/web/documents.html','utf8');for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g))nodes.set(match[2],element(match[1]));
 nodes.get('pdfs').previousElementSibling=element('label');
 const context=vm.createContext({document:{getElementById:id=>nodes.get(id),createElement:element,querySelector:()=>null,querySelectorAll:selector=>selector==='button,input,select,textarea'?controls:[]},fetch:()=>new Promise(resolve=>{resolveFetch=resolve;}),Option:function(text,value){return {text,value};},URLSearchParams,setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer;},clearTimeout:id=>timers.delete(id),location:{search:''}});
 vm.runInContext(fs.readFileSync('src/mcdxkit/web/documents.js','utf8'),context);
 return {context,nodes,element,timers,tick:async()=>{const [id,fn]=timers.entries().next().value;timers.delete(id);await fn();},finish:(body)=>resolveFetch({ok:!!body,json:async()=>body})};
}
test('controls created while a request is running remain disabled until completion',async()=>{
 const {context,finish}=harness();
 // Initial connect is intentionally pending; simulate a render during that request.
 vm.runInContext("var lateButton=action('Open PDF',()=>{}); lockControls();",context);
 assert.equal(vm.runInContext('lateButton.disabled',context),true);
 finish();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(vm.runInContext('lateButton.disabled',context),false);
});
test('notebook section navigation preserves the other visible sections',()=>{
 const {context,nodes}=harness();
 vm.runInContext("project={id:'example'}; tab('source');",context);
 for(const key of ['answers','source','draft','packages'])assert.equal(nodes.get(key+'-view').hidden,false);
});
test('switching PDFs releases the PDF.js loading task, not the document proxy',async()=>{
 const {context,nodes}=harness();
 nodes.get('pdf-canvas').getContext=()=>({clearRect(){}});
 vm.runInContext('var released=false; pdfDocument={loadingTask:{destroy:async()=>{released=true;}}}; pdfBlob={};',context);
 await vm.runInContext('releasePDF()',context);
 assert.equal(vm.runInContext('released',context),true);
 assert.equal(vm.runInContext('pdfDocument',context),null);
 assert.equal(vm.runInContext('pdfBlob',context),null);
});

test('discovery preserves a valid model selection and offers retry after a failure',async()=>{
 const {context,nodes,finish}=harness();finish();await new Promise(resolve=>setImmediate(resolve));
 nodes.get('router-model').value='example/model:free';
 const loading=vm.runInContext('loadModels()',context);
 finish({models:[{id:'example/model:free',name:'Example'}],embedding_models:[{id:'example/embed:free',name:'Search',context_length:8192}]});await loading;
 assert.equal(nodes.get('router-model').value,'example/model:free');
 assert.equal(nodes.get('router-model').children.length,2);
 assert.equal(nodes.get('router-embedding').value,'example/embed:free');
 assert.match(nodes.get('model-status').textContent,/1 free text models/);
 const failing=vm.runInContext('loadModels()',context);finish({error:'Rate limited'});await failing;
 assert.match(nodes.get('model-status').textContent,/Retry with Refresh models/);
 assert.equal(nodes.get('router-model').children.length,2);
});

test('tested defaults are offered for new settings without replacing existing selections',async()=>{
 const {context,nodes,finish}=harness();finish();await new Promise(resolve=>setImmediate(resolve));
 const catalog={models:[{id:'tested/answer:free',name:'Answer',recommendation:'Default · tested 2026-10-14'},{id:'chosen/answer:free',name:'Chosen'}],embedding_models:[{id:'tested/search:free',name:'Search',recommendation:'Default · tested 2026-10-14'},{id:'chosen/search:free',name:'Chosen'}]};
 let loading=vm.runInContext('loadModels()',context);finish(catalog);await loading;
 assert.equal(nodes.get('router-model').value,'tested/answer:free');
 assert.equal(nodes.get('router-embedding').value,'tested/search:free');
 assert.match(nodes.get('router-model').children[1].text,/tested 2026-10-14/);
 nodes.get('router-model').value='chosen/answer:free';nodes.get('router-embedding').value='chosen/search:free';
 loading=vm.runInContext('loadModels()',context);finish(catalog);await loading;
 assert.equal(nodes.get('router-model').value,'chosen/answer:free');
 assert.equal(nodes.get('router-embedding').value,'chosen/search:free');
 // A disappearing provider never silently changes an existing model/index.
 nodes.get('router-model').value='unavailable/answer:free';nodes.get('router-embedding').value='unavailable/search:free';
 loading=vm.runInContext('loadModels()',context);finish(catalog);await loading;
 assert.notEqual(nodes.get('router-model').value,'tested/answer:free');
 assert.notEqual(nodes.get('router-embedding').value,'tested/search:free');
});

async function indexingHarness(){
 const h=harness();h.finish();await new Promise(resolve=>setImmediate(resolve));
 vm.runInContext("project={id:'library',revision:1,sources:[{id:'pdf',name:'Plans.pdf',status:'parsing',progress:0}],drafts:[],runs:[]}; connections={ragflow_configured:true,openrouter_configured:true}; reviewDirty=true;",h.context);
 h.nodes.get('question').value='Keep my question';
 return h;
}
test('index progress refreshes without replacing unsaved work, and stops when complete',async()=>{
 const h=await indexingHarness();
 vm.runInContext('scheduleIndexStatus()',h.context);
 assert.equal(h.timers.size,1);
 const pending=h.tick();
 h.finish({id:'library',revision:1,sources:[{id:'pdf',status:'ready',progress:1}]});await pending;
 assert.equal(h.nodes.get('ask').disabled,false);
 assert.equal(h.nodes.get('question').value,'Keep my question');
 assert.equal(vm.runInContext('reviewDirty',h.context),true);
 assert.equal(h.timers.size,0);
 assert.match(h.nodes.get('source-count').textContent,/1 indexed/);
});
test('late progress from another project cannot replace the current project',async()=>{
 const h=await indexingHarness();vm.runInContext('scheduleIndexStatus()',h.context);
 const pending=h.tick();
 vm.runInContext("project={id:'other',revision:2,sources:[],drafts:[],runs:[]}",h.context);
 h.finish({id:'library',revision:1,sources:[{id:'pdf',status:'ready',progress:1}]});await pending;
 assert.equal(vm.runInContext('project.id',h.context),'other');
 assert.equal(vm.runInContext('project.sources.length',h.context),0);
});
test('status polling defers during actions and stops after a network error',async()=>{
 const h=await indexingHarness();vm.runInContext('busy=true;scheduleIndexStatus()',h.context);
 await h.tick();assert.equal(h.timers.size,1);
 vm.runInContext('busy=false',h.context);const pending=h.tick();h.finish();await pending;
 assert.equal(h.timers.size,0);
 assert.match(h.nodes.get('notice').textContent,/Refresh status/);
 assert.equal(vm.runInContext('project.sources[0].status',h.context),'parsing');
});


test('package worksheet preview loads the verified result in a sandbox and reuses its frame',async()=>{
 const h=harness();h.finish();await new Promise(resolve=>setImmediate(resolve));
 vm.runInContext("project={id:'library',sources:[],runs:[{id:'run',created:0,check_summary:{total:0}}]};renderPackages();",h.context);
 const row=h.nodes.get('packages-view').children[0],buttons=row.children.find(n=>n.className==='actions');
 const preview=buttons.children.find(n=>n.textContent==='View calculated worksheet');
 assert.ok(preview,'Outputs must open the full calculated worksheet, not only the narrative report');
 vm.runInContext("var previewRequests=[];fetch=async url=>{previewRequests.push(url);return {ok:true,text:async()=>'<p>Computed result</p>'};};",h.context);
 await preview.events.click();
 const frame=row.children.find(n=>n.tagName==='iframe');
 assert.equal(frame.title,'Calculated worksheet');
 assert.equal(frame.attributes.sandbox,'');
 assert.equal(frame.srcdoc,'<p>Computed result</p>');
 assert.equal(vm.runInContext('previewRequests[0]',h.context),'/api/documents/library/runs/run/calculation.html');
 await preview.events.click();
 assert.equal(row.children.filter(n=>n.tagName==='iframe').length,1);
 vm.runInContext("fetch=async()=>({ok:false,json:async()=>({error:'Package changed after generation.'})});",h.context);
 await preview.events.click();
 assert.match(h.nodes.get('notice').textContent,/Package changed/);
 assert.equal(frame.srcdoc,'');
});
