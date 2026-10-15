import {render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {test,expect,vi,beforeEach,afterEach} from 'vitest';
import App from './App';

beforeEach(()=>{history.replaceState(null,'','/');localStorage.clear();});
afterEach(()=>{vi.unstubAllGlobals();});

test('project tasks stay central and original sources open only on demand', async()=>{
  const project={id:'a'.repeat(32),title:'Synthetic foundation',revision:1,sources:[{id:'b'.repeat(32),name:'Design specification.pdf',status:'uploaded'}],answers:[],drafts:[],runs:[],calculations:[]};
  vi.stubGlobal('fetch', async (path:string,options?:RequestInit)=>{
    const data=path==='/api/session'?{token:'token'}:path==='/api/documents'?{projects:[project],configuration:{}}:path==='/api/chatgpt'?{available:true,accounts:[],active:null}:path==='/api/documents/show'?project:path.startsWith('/api/jobs')?[]:{};
    return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
  });
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App/></QueryClientProvider>);
  expect(await screen.findByRole('heading',{name:'Synthetic foundation'})).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Ask'}));
  expect(screen.getByRole('textbox',{name:'Ask a question'})).toBeVisible();
  expect(screen.getByText(/0 of 1 documents ready/)).toBeVisible();
  expect(screen.getByRole('button',{name:'Send question'})).toBeDisabled();
  await userEvent.type(screen.getByRole('textbox',{name:'Ask a question'}),'What should I do with the file I uploaded?');
  expect(screen.getByRole('button',{name:'Send question'})).toBeEnabled();
  expect(screen.queryByRole('complementary',{name:'Source documents'})).toBeNull();
  await userEvent.click(screen.getByRole('button',{name:'Sources'}));
  expect(screen.getByRole('complementary',{name:'Source documents'})).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Close sources'}));
  expect(screen.queryByRole('complementary',{name:'Source documents'})).toBeNull();
  await waitFor(()=>expect(screen.getByRole('button',{name:'Sources'})).toHaveFocus());
  vi.unstubAllGlobals();
});


function scenario({empty=false,inspectError=false,failedRun=false,jobs=[] as any[],two=false,library=[] as any[],inputs=[] as any[]}={}){
  let project:any={id:'c'.repeat(32),title:'Two-file calculation',revision:1,sources:[],answers:[],drafts:[],runs:[],calculations:[]};
  const calculation={id:'d'.repeat(32),name:'Loads.txt + Template.mcdx',created:1,report_name:'Loads.txt',template_name:'Template.mcdx',template_inputs:{inputs,input_map:{}},options:{cases:[2],input_map:null,load_source:'local'}};
  const second={...calculation,id:'e'.repeat(32),name:'Service loads',options:{cases:[7],input_map:null,load_source:'local'}};
  if(two)project={...project,calculations:[calculation,second],runs:[
    {id:'r1',created:1,draft_id:null,calculation_id:calculation.id,calculated:true},{id:'r2',created:2,draft_id:null,calculation_id:second.id,calculated:true}]};
  if(failedRun)project={...project,calculations:[calculation],runs:[{id:'failed',created:1,draft_id:null,calculation_id:calculation.id,calculated:false}]};
  const calls:{path:string;data:any}[]=[];
  const response=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
  vi.stubGlobal('fetch',async(path:string,options?:RequestInit)=>{
    const data=typeof options?.body==='string'?JSON.parse(options.body):options?.body;
    calls.push({path,data});
    if(path==='/api/session')return response({token:'token'});
    if(path==='/api/documents')return response({projects:empty?[]:[project],configuration:{}});
    if(path==='/api/chatgpt')return response({available:true,accounts:[],active:null});
    if(path==='/api/documents/show')return response(project);
    if(path.startsWith('/api/upload?kind=report'))return response({id:'raw',name:'Loads.txt',inspection:{cases:[{id:2,name:'Local case'}],selected_cases:[2]}});
    if(path==='/api/templates')return response({templates:library});
    if(path.startsWith('/api/templates/upload'))return response({id:'t'.repeat(64),name:'Template.mcdx'});
    if(path.startsWith('/api/templates/')&&path.endsWith('/use'))return response({id:'template',name:'Template.mcdx',template_id:'t'.repeat(64)});
    if(path==='/api/inspect')return inspectError?response({error:'Incompatible template dimensions'},400):response({cases:[{id:2,name:'Local case'}],selected_cases:[2],envelope:{},template_inputs:{inputs,input_map:{}},template_validation:{status:'ready',errors:[],warnings:[]}});
    if(path==='/api/documents/connect'){
      project={...project,calculations:[calculation]};
      return response({...project,calculation});
    }
    if(path==='/api/documents/calculation')return response({report:{id:'raw'},template:{id:'template'}});
    if(path==='/api/documents/run-file')return response({id:'delivered',name:'calculation.mcdx',kind:'worksheet'});
    if(path==='/api/view')return response({name:'Template.mcdx',page:{width:600,height:800,content_height:700,margins:[30,30,30,30],count:1},regions:[],header:[],footer:[],has_cached_results:false,view:'template'});
    if(path.startsWith('/api/jobs'))return response(jobs);
    return response({});
  });
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  const rendered=render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
  return {...rendered,calls,client};
}

async function uploadPair(container:HTMLElement){
  await userEvent.upload(container.querySelector('input[type="file"][accept=".gp11t,.txt"]') as HTMLInputElement,new File(['Synthetic raw report'],'Loads.txt',{type:'text/plain'}));
  await userEvent.upload(container.querySelector('input[type="file"][accept=".mcdx"]') as HTMLInputElement,new File(['Synthetic template placeholder'],'Template.mcdx'));
}

test('Ask is a separate destination before a project exists and survives a direct URL',async()=>{
  history.replaceState(null,'','/?view=ask');
  scenario({empty:true});
  await screen.findByRole('button',{name:'Create a project'});
  const ask=screen.getByRole('button',{name:'Ask'});
  expect(ask).toHaveAttribute('aria-current','page');
  expect(screen.getByRole('navigation',{name:'Ask'})).toContainElement(ask);
  expect(screen.getByRole('navigation',{name:'Calculation'})).not.toContainElement(ask);
  await userEvent.click(screen.getByRole('button',{name:'Inputs'}));
  expect(ask).toBeEnabled();
  await userEvent.click(ask);
  expect(new URLSearchParams(location.search).get('view')).toBe('ask');
});

test('Ask remains accessible from every calculation and reference view without uploaded PDFs',async()=>{
  scenario();
  await screen.findByRole('heading',{name:'Calculation inputs'});
  expect(screen.getByRole('button',{name:'Choose raw report'})).toBeVisible();
  expect(screen.getByRole('button',{name:'Choose Mathcad template'})).toBeVisible();
  expect(screen.getByRole('button',{name:'Connect calculation'})).toBeDisabled();
  for(const name of ['Inputs','Worksheet','Outputs','Requirements']){
    await userEvent.click(screen.getByRole('button',{name}));
    const ask=screen.getByRole('button',{name:'Ask'});
    expect(ask).toBeVisible();expect(ask).toBeEnabled();
    await userEvent.click(ask);
    expect(screen.getByRole('textbox',{name:'Ask a question'})).toBeVisible();
    expect(ask).toHaveAttribute('aria-current','page');
  }
});

test('the raw report and template can be checked and calculated with no reference documents',async()=>{
  const {container,calls}=scenario();
  await screen.findByRole('heading',{name:'Calculation inputs'});
  await uploadPair(container);
  const connect=screen.getByRole('button',{name:'Connect calculation'});
  await waitFor(()=>expect(connect).toBeEnabled());
  await userEvent.click(screen.getByRole('button',{name:'Ask'}));
  await userEvent.click(screen.getByRole('button',{name:'Inputs'}));
  expect(screen.getByText('Loads.txt',{selector:'strong'})).toBeVisible();
  expect(screen.getByText('Template.mcdx',{selector:'strong'})).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Connect calculation'}));
  const calculate=await screen.findByRole('button',{name:'Calculate'});
  expect(calls.find(c=>c.path==='/api/documents/connect')?.data).toEqual({project:'c'.repeat(32),id:'raw',library_template:'t'.repeat(64),cases:[2]});
  // The uploaded template went into the workspace library first, then the calculation used it from there.
  expect(calls.some(c=>c.path.startsWith('/api/templates/upload?name=Template.mcdx'))).toBe(true);
  await userEvent.click(calculate);
  await screen.findByRole('heading',{name:'Calculated outputs'});
  expect(calls.find(c=>c.path==='/api/jobs'&&c.data?.action==='calculate')?.data).toEqual(expect.objectContaining({project:'c'.repeat(32),action:'calculate',payload:{calculation:'d'.repeat(32)}}));
  expect(calls.some(c=>c.path==='/api/jobs'&&['ingest','extract'].includes(c.data?.action))).toBe(false);
  expect(screen.getByRole('button',{name:'Ask'})).toBeEnabled();
});

test('failed input checks do not leave a checking status or enable connection',async()=>{
  const {container}=scenario({inspectError:true});
  await screen.findByRole('heading',{name:'Calculation inputs'});
  await uploadPair(container);
  await screen.findAllByText(/Incompatible template dimensions/);
  expect(screen.getByRole('button',{name:'Connect calculation'})).toBeDisabled();
  expect(screen.queryByText(/Checking load cases and input compatibility/)).not.toBeInTheDocument();
});

test('failed calculation runs never mark the Calculated workflow step complete',async()=>{
  scenario({failedRun:true});
  const steps=await screen.findByRole('list',{name:'Calculation steps'});
  const last=steps.querySelectorAll('li')[3];
  expect(last).not.toHaveTextContent('done');
  await userEvent.click(screen.getByRole('button',{name:'Ask'}));
  // Ask names the project and calculation only; run counts and cases are not repeated there.
  expect(screen.getByText(/Asking about/)).toHaveTextContent('Asking about Two-file calculation · Loads.txt + Template.mcdx');
});


test('choosing Ask while project details load wins over automatic Inputs landing',async()=>{
  scenario();
  const original=globalThis.fetch;
  let finish!:()=>void;
  const held=new Promise<void>(resolve=>{finish=resolve;});
  vi.stubGlobal('fetch',async(path:string,options?:RequestInit)=>{
    if(path==='/api/documents/show')await held;
    return original(path,options);
  });
  const ask=screen.getByRole('button',{name:'Ask'});
  await userEvent.click(ask);
  finish();
  await screen.findByRole('heading',{name:'Two-file calculation'});
  await screen.findByRole('textbox',{name:'Ask a question'});
  expect(ask).toHaveAttribute('aria-current','page');
  expect(new URLSearchParams(location.search).get('view')).toBe('ask');
});


test('Outputs shows calculation failures but keeps unrelated chat failures in Ask',async()=>{
  history.replaceState(null,'','/?view=outputs');
  scenario({jobs:[
    {id:'chat-failure',action:'ask',state:'failed',stage:'Stopped',error:'Chat-only failure',uncertain:false},
    {id:'calculation-failure',action:'calculate',state:'failed',stage:'Stopped',error:'Calculation-only failure',uncertain:false},
  ]});
  await screen.findByRole('heading',{name:'Calculated outputs'});
  await screen.findAllByText('Calculation-only failure');
  expect(screen.queryByText('Chat-only failure')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Ask'})).toBeEnabled();
});

test('a project holds several calculations; outputs and calculate follow the selected one',async()=>{
  history.replaceState(null,'','/?view=outputs');
  const {calls}=scenario({two:true});
  await screen.findByRole('heading',{name:'Calculated outputs'});
  const picker=screen.getByRole('combobox',{name:'Calculation'});
  expect(picker).toHaveValue('e'.repeat(32));                       // newest by default
  expect(screen.getByRole('combobox',{name:'Calculation run'}).querySelectorAll('option')).toHaveLength(1);
  await userEvent.selectOptions(picker,'d'.repeat(32));
  expect(screen.getByText(/Loads.txt \+ Template.mcdx · 2 calculations/)).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Calculate this'}));
  await waitFor(()=>expect(calls.find(c=>c.path==='/api/jobs'&&c.data?.action==='calculate')?.data.payload).toEqual({calculation:'d'.repeat(32)}));
  // Adding another calculation opens an empty two-file slot without touching the existing ones.
  await userEvent.selectOptions(picker,'new');
  expect(await screen.findByRole('heading',{name:'New calculation'})).toBeVisible();
  expect(screen.getByRole('button',{name:'Choose raw report'})).toBeVisible();
  expect(calls.some(c=>c.path==='/api/documents/connect')).toBe(false);
});

test('the template library opens without a project and shows a template\'s inputs',async()=>{
  history.replaceState(null,'','/?view=templates');
  const template={id:'t'.repeat(64),name:'Pile cap.mcdx',sha256:'t'.repeat(64),bytes:10,created:1,archived:false,version:1,calculations:3,mapping_basis:'fixed-head pile preset',missing_load_inputs:[],
    inputs:[{variable:'P_a',value:10,unit:'kip',component:'P',status:'matched',expression:'P_a := 10 kip'},{variable:'n_z',value:3,unit:'1',component:null,status:'retained',expression:'n_z := 3'}]};
  const {calls}=scenario({empty:true,library:[template]});
  expect(await screen.findByRole('heading',{name:'Template library'})).toBeVisible();
  expect(screen.getByRole('button',{name:/^Templates/})).toHaveAttribute('aria-current','page');
  expect(await screen.findByText(/2 inputs · 3 calculations/)).toBeVisible();
  expect(screen.getByRole('cell',{name:/P_a/})).toBeVisible();
  expect(screen.getByRole('button',{name:'Create a project to use it'})).toBeVisible();
  await waitFor(()=>expect(calls.some(c=>c.path===`/api/templates/${'t'.repeat(64)}/use`)).toBe(true));
  await waitFor(()=>expect(calls.some(c=>c.path==='/api/view')).toBe(true));
});

test('a calc= link opens that calculation, not the newest',async()=>{
  history.replaceState(null,'','/?project='+'c'.repeat(32)+'&calc='+'d'.repeat(32)+'&view=outputs');
  scenario({two:true});
  await screen.findByRole('heading',{name:'Calculated outputs'});
  await waitFor(()=>expect(screen.getByRole('combobox',{name:'Calculation'})).toHaveValue('d'.repeat(32)));
  expect(new URLSearchParams(location.search).get('calc')).toBe('d'.repeat(32));
});

test('outputs render the Mathcad file the engineer downloads',async()=>{
  history.replaceState(null,'','/?view=outputs');
  const {calls}=scenario({two:true});
  await screen.findByRole('heading',{name:'Calculated outputs'});
  expect(screen.getByRole('button',{name:'Download Mathcad file'})).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Mathcad file'}));
  expect(await screen.findByText(/This is calculation.mcdx, the Mathcad file you download/)).toBeVisible();
  expect(calls.find(c=>c.path==='/api/documents/run-file')?.data).toEqual({project:'c'.repeat(32),run:'r2'});
  expect(calls.find(c=>c.path==='/api/view'&&c.data?.id==='delivered')).toBeTruthy();
});

test('Calculate all sits beside Calculate this and pins every calculation in the project',async()=>{
  const {calls}=scenario({two:true});
  const button=await screen.findByRole('button',{name:'Calculate all (2)'});
  const single=screen.getByRole('button',{name:'Calculate this'});
  expect(button.closest('.header-actions')).toContainElement(single);
  await userEvent.click(button);
  await waitFor(()=>expect(calls.find(c=>c.path==='/api/jobs'&&c.data?.action==='calculate_all')?.data).toEqual(
    expect.objectContaining({project:'c'.repeat(32),action:'calculate_all',payload:{calculations:['d'.repeat(32),'e'.repeat(32)]},request_key:expect.any(String)})));
  expect(await screen.findByRole('heading',{name:'Calculated outputs'})).toBeVisible();
});

test('Calculate all is offered only with several calculations and waits for running calculation work',async()=>{
  scenario();
  await screen.findByRole('heading',{name:'Calculation inputs'});
  expect(screen.queryByRole('button',{name:/Calculate all/})).toBeNull();
  vi.unstubAllGlobals();
  scenario({two:true,jobs:[{id:'running',action:'calculate_all',state:'running',stage:'Finished 1 of 2 calculations',uncertain:false,
    activity:[{time:1,message:'Finished 0 of 2 calculations',detail:{finished:0,total:2}},{time:2,message:'Finished 1 of 2 calculations',detail:{finished:1,total:2}}]}]});
  expect(await screen.findByRole('button',{name:'Calculate all (2)'})).toBeDisabled();
  const bar=screen.getByRole('progressbar',{name:'Finished 1 of 2 calculations'});
  expect(bar).toHaveAttribute('value','1');expect(bar).toHaveAttribute('max','2');
});

const finishedAll=(failed:boolean)=>({id:'all',action:'calculate_all',state:'succeeded',stage:'Complete',uncertain:false,created:10,result:{total:2,succeeded:failed?0:1,skipped:1,failed:failed?1:0,calculations:[
  {calculation_id:'d'.repeat(32),name:'Loads.txt + Template.mcdx',status:'skipped',run_id:'r1',error:null},
  {calculation_id:'e'.repeat(32),name:'Service loads',status:failed?'failed':'succeeded',run_id:failed?null:'r2',error:failed?'Synthetic engine failure':null}]}});

test('Outputs summarises the latest Calculate all, names failures and marks them in the picker',async()=>{
  history.replaceState(null,'','/?view=outputs');
  scenario({two:true,jobs:[finishedAll(true)]});
  const summary=await screen.findByRole('region',{name:'Calculate all results'});
  expect(summary).toHaveTextContent('Calculate all · 0 calculated, 1 already calculated, 1 failed');
  expect(summary).toHaveTextContent('a saved run with the same inputs and engine was reused');
  expect(screen.getByRole('alert')).toHaveTextContent('Synthetic engine failure');
  const picker=screen.getByRole('combobox',{name:'Calculation'});
  expect([...picker.querySelectorAll('option')].map(o=>o.textContent)).toEqual(['Loads.txt + Template.mcdx · calculated','Service loads · failed','+ New calculation']);
  // Only failures are listed until the engineer expands the summary, so a long project never buries its outputs.
  expect(within(summary).queryByRole('button',{name:'Loads.txt + Template.mcdx'})).toBeNull();
  await userEvent.click(within(summary).getByRole('button',{name:'Show all 2'}));
  // A row opens its calculation; Dismiss hides the summary, also after a reload.
  await userEvent.click(within(summary).getByRole('button',{name:'Loads.txt + Template.mcdx'}));
  expect(picker).toHaveValue('d'.repeat(32));
  await userEvent.click(within(summary).getByRole('button',{name:'Dismiss'}));
  expect(screen.queryByRole('region',{name:'Calculate all results'})).toBeNull();
  expect(JSON.parse(localStorage.getItem('mcdxkit.dismissed-calculate-all')||'[]')).toEqual(['all']);
});

test('a fully successful Calculate all still leaves a summary',async()=>{
  history.replaceState(null,'','/?view=outputs');
  scenario({two:true,jobs:[finishedAll(false)]});
  const summary=await screen.findByRole('region',{name:'Calculate all results'});
  expect(summary).toHaveTextContent('1 calculated, 1 already calculated, 0 failed');
  expect(screen.queryByRole('alert')).toBeNull();
});

test('a template shows its inputs and worksheet side by side',async()=>{
  history.replaceState(null,'','/?view=templates');
  const template={id:'t'.repeat(64),name:'Pile cap.mcdx',sha256:'t'.repeat(64),bytes:10,created:1,archived:false,version:1,calculations:0,mapping_basis:null,missing_load_inputs:[],
    inputs:[{variable:'P_a',value:10,unit:'kip',component:'P',status:'matched',expression:'P_a := 10 kip'}]};
  scenario({empty:true,library:[template]});
  const inputs=await screen.findByRole('region',{name:'Template inputs'});
  const sheet=screen.getByRole('region',{name:'Template worksheet'});
  expect(inputs.parentElement).toBe(sheet.parentElement);
  expect(inputs.parentElement).toHaveClass('template-panes');
  expect(within(inputs).getByRole('heading',{name:'Inputs · 1'})).toBeVisible();
  expect(within(inputs).getByText('P_a')).toBeVisible();
});

const searchable=[{variable:'P_a',value:10,unit:'kip',component:'P',status:'matched',expression:'P_a := 10 kip'},
  {variable:'M_z',value:4,unit:'kip*ft',component:'Mz',status:'matched',expression:'M_z := 4 kip*ft'},
  {variable:'n_z',value:3,unit:'1',component:null,status:'retained',expression:'n_z := 3'}];

test('calculation inputs can be searched by variable, unit or load source',async()=>{
  history.replaceState(null,'','/?view=inputs');
  scenario({two:true,inputs:searchable});
  const search=await screen.findByRole('searchbox',{name:'Search inputs'});
  const rows=()=>screen.getAllByRole('row').slice(1).map(r=>r.querySelector('strong')?.textContent);
  expect(rows()).toEqual(['P_a','M_z','n_z']);
  await userEvent.type(search,'kip');
  expect(rows()).toEqual(['P_a','M_z']);
  expect(screen.getByText('2 of 3 inputs')).toBeVisible();
  await userEvent.clear(search);await userEvent.type(search,'mz');
  expect(rows()).toEqual(['M_z']);
  await userEvent.clear(search);await userEvent.type(search,'nothing here');
  expect(screen.getByText(/No inputs match/)).toBeVisible();
});

test('template inputs can be searched in the library',async()=>{
  history.replaceState(null,'','/?view=templates');
  scenario({empty:true,library:[{id:'t'.repeat(64),name:'Pile cap.mcdx',sha256:'t'.repeat(64),bytes:10,created:1,archived:false,version:1,calculations:0,mapping_basis:null,missing_load_inputs:[],inputs:searchable}]});
  const pane=await screen.findByRole('region',{name:'Template inputs'});
  await userEvent.type(within(pane).getByRole('searchbox',{name:'Search inputs'}),'n_');
  expect(within(pane).getAllByRole('row').slice(1).map(r=>r.querySelector('strong')?.textContent)).toEqual(['n_z']);
});
