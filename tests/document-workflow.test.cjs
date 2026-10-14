// Real document UI orchestration with a small DOM boundary; layout is browser-tested.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(){
 const controls=[],nodes=new Map();let resolveFetch;
 function element(tag='div') {const e={tagName:tag,hidden:false,disabled:false,isConnected:true,value:'',dataset:{},children:[],classList:{add(){},remove(){},toggle(){}},setAttribute(){},addEventListener(){},append(...kids){this.children.push(...kids);},replaceChildren(...kids){this.children=kids;},querySelector(){return null;},close(){},scrollIntoView(){}};if(['button','input','select','textarea'].includes(tag))controls.push(e);return e;}
 const html=fs.readFileSync('src/mcdxkit/web/documents.html','utf8');for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g))nodes.set(match[2],element(match[1]));
 nodes.get('pdfs').previousElementSibling=element('label');
 const context=vm.createContext({document:{getElementById:id=>nodes.get(id),createElement:element,querySelector:()=>null,querySelectorAll:selector=>selector==='button,input,select,textarea'?controls:[]},fetch:()=>new Promise(resolve=>{resolveFetch=resolve;}),Option:function(text,value){return {text,value};},URLSearchParams,setTimeout,location:{search:''}});
 vm.runInContext(fs.readFileSync('src/mcdxkit/web/documents.js','utf8'),context);
 return {context,nodes,element,finish:(body)=>resolveFetch({ok:!!body,json:async()=>body})};
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
