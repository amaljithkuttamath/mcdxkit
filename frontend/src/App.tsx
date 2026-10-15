import {useEffect, useRef, useState} from 'react';
import {Button, Checkbox, Input as AriaInput, Label, SearchField, TextArea, TextField} from 'react-aria-components';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {api, download, request, sessionToken, streamConversation} from './api';
import {CalculateAllSummary, FactValue, Icon, JobStatus, RequirementRow} from './components';
import {Calculated, PDFPage, Worksheet} from './viewers';
import {Ask} from './Ask';
import {ChatGPTSettings, ChatGPTWelcome} from './ChatGPT';
import type {ChatGPTStatus, ReturnTo} from './ChatGPT';
import type {WorksheetData} from './viewers';
import type {CalculateAllResult, Calculation, Draft, Fact, Inspection, Inputs, Job, LibraryTemplate, Project, Source, Upload, WorkflowAction} from './types';

type Tab='worksheet'|'inputs'|'requirements'|'answers'|'outputs'|'templates';
type SourceSelection={id:string;page:number;quote:string};
// Jobs that produce calculated outputs. Outputs shows only their failures; Ask failures stay in Ask.
const CALCULATION_ACTIONS=['calculate','build','calculate_all'];
type Configuration={model?:string;router_key_saved?:boolean;ragflow_configured?:boolean;provider?:string;chatgpt_model?:string};
function Field({label,value,onChange,area=false,type='text'}:{label:string;value:string;onChange:(value:string)=>void;area?:boolean;type?:'text'|'password'}) {
  return <TextField value={value} onChange={onChange} type={type}><Label>{label}</Label>{area?<TextArea rows={3}/>:<AriaInput/>}</TextField>;
}

// Filters a long input list by variable, expression, unit or load source; case-insensitive substring.
function matchesInput(i:{variable:string;expression?:string;unit?:unknown;component?:string|null},query:string){
  const q=query.trim().toLowerCase();
  return !q||[i.variable,i.expression,typeof i.unit==='string'?i.unit:JSON.stringify(i.unit??''),i.component].some(v=>String(v??'').toLowerCase().includes(q));
}
function InputSearch({value,onChange,shown,total}:{value:string;onChange:(v:string)=>void;shown:number;total:number}) {
  return <div className="input-search"><SearchField aria-label="Search inputs" value={value} onChange={onChange}><AriaInput placeholder="Search inputs, e.g. P_a, kip, Mz"/><Button aria-label="Clear search" className="clear">×</Button></SearchField>
    {value.trim()&&<span role="status">{shown} of {total} inputs</span>}</div>;
}

export default function App() {
  const client=useQueryClient();
  const initial=new URLSearchParams(location.search);
  const [pid,setPid]=useState(initial.get('project')||initial.get('library')||''),[tab,setTabState]=useState<Tab>(initial.get('view')==='ask'?'answers':(['worksheet','inputs','requirements','outputs','templates'].includes(initial.get('view')||'')?initial.get('view') as Tab:'worksheet'));
  const [drawer,setDrawer]=useState(false),[source,setSource]=useState<SourceSelection|null>(null),[notice,setNotice]=useState(''),[task,setTask]=useState(''),[askPrompt,setAskPrompt]=useState('');
  const [view,setView]=useState('template'),[outputView,setOutputView]=useState<'report'|'mathcad'>('report'),[selectedRun,setSelectedRun]=useState(''),[newTitle,setNewTitle]=useState(''),[creating,setCreating]=useState(false);
  const [report,setReport]=useState<Upload|null>(null),[template,setTemplate]=useState<Upload|null>(null),[cases,setCases]=useState<number[]>([]),[mapping,setMapping]=useState<Record<string,string>|null>(null);
  const [selection,setSelection]=useState<string[]>([]),[reviewFact,setReviewFact]=useState<Fact|null>(null);
  // Selected calculation; 'new' adds another report + template to the project. Empty means the newest.
  const [calcId,setCalcId]=useState(initial.get('calc')||'');
  // A calc= link survives the first load and the automatic project selection; switching projects clears it.
  const shownPid=useRef(initial.get('project')||'');
  const sourcesButton=useRef<HTMLButtonElement>(null),opener=useRef<HTMLElement|null>(null),files=useRef<HTMLInputElement>(null);
  const reportFile=useRef<HTMLInputElement>(null),templateFile=useRef<HTMLInputElement>(null);
  // Without an explicit view (?view= or any navigation), a project with no connected calculation opens once on
  // Inputs, where the two files go. Every explicit navigation goes through setTab and wins over that landing,
  // even when project data arrives later (e.g. Ask chosen before a project exists or loads).
  const [explicit,setExplicit]=useState(!!initial.get('view')),landed=useRef(explicit);
  function setTab(next:Tab){landed.current=true;setExplicit(true);setTabState(next);}
  const session=useQuery({queryKey:['session'],queryFn:async()=>{const s=await api<{token:string}>('/api/session');sessionToken(s.token);return s;},retry:false});
  const libraries=useQuery({queryKey:['libraries'],queryFn:()=>api<{projects:Project[];configuration:Configuration}>('/api/documents'),enabled:!!session.data});
  const chatgpt=useQuery({queryKey:['chatgpt'],queryFn:()=>api<ChatGPTStatus>('/api/chatgpt'),enabled:!!session.data});
  // Returning from ChatGPT sign-in (?chatgpt=...) reopens Services on the same project/view,
  // with or without projects, until the user closes it.
  const returning=useRef(!!initial.get('chatgpt'));
  useEffect(()=>{if(returning.current&&session.data)setTask('settings');},[session.data]);
  const library=useQuery({queryKey:['templates'],queryFn:()=>api<{templates:LibraryTemplate[]}>('/api/templates'),enabled:!!session.data});
  const project=useQuery({queryKey:['project',pid],queryFn:()=>api<Project>('/api/documents/show',{project:pid}),enabled:!!pid&&!!session.data});
  const jobs=useQuery({queryKey:['jobs',pid],queryFn:()=>api<Job[]>('/api/jobs?project='+pid),enabled:!!pid&&!!session.data,refetchInterval:1500});
  const lastStates=useRef('');
  useEffect(()=>{const states=JSON.stringify(jobs.data?.map(j=>[j.id,j.state]));if(states!==lastStates.current){lastStates.current=states;client.invalidateQueries({queryKey:['project',pid]});}},[jobs.data,pid,client]);
  useEffect(()=>{if(!pid&&libraries.data?.projects.length)setPid(libraries.data.projects[0].id);},[libraries.data,pid]);
  useEffect(()=>{setSource(null);setDrawer(false);setSelection([]);setReport(null);setTemplate(null);setMapping(null);setSelectedRun('');if(shownPid.current!==pid){if(shownPid.current)setCalcId('');shownPid.current=pid;}setTask(t=>returning.current&&t==='settings'?t:'');setReviewFact(null);setNotice('');},[pid]);
  // The view is bookmarkable with or without a project. An automatic landing is not written, so a reload lands again.
  useEffect(()=>{
    const query=[pid&&'project='+pid,calcId&&'calc='+calcId,explicit&&'view='+(tab==='answers'?'ask':tab)].filter(Boolean).join('&');
    if(query)history.replaceState(null,'','/?'+query);
  },[pid,tab,explicit,calcId]);
  const p=project.data,active=jobs.data?.filter(j=>['queued','running'].includes(j.state))||[];
  const calcs=p?.calculations||[];
  const calc:Calculation|undefined=calcId==='new'?undefined:calcs.find(c=>c.id===calcId)||calcs.at(-1);
  useEffect(()=>{if(!landed.current&&p){landed.current=true;if(!p.calculations.length)setTabState('inputs');}},[p]);
  const conversation=active.find(j=>j.action==='ask');
  useEffect(()=>{
    if(!conversation)return;
    const abort=new AbortController();
    streamConversation(conversation.id,event=>client.setQueryData<Job[]>(['jobs',pid],old=>old?.map(j=>j.id===event.id?event:j)),abort.signal).catch(e=>{if(!abort.signal.aborted)setNotice(e.message);});
    return()=>abort.abort();
  },[conversation?.id,pid,client]);
  const work=useMutation({mutationFn:async(fn:()=>Promise<unknown>)=>fn(),onSuccess:()=>{client.invalidateQueries({queryKey:['project',pid]});client.invalidateQueries({queryKey:['libraries']});client.invalidateQueries({queryKey:['jobs',pid]});},onError:(e)=>setNotice(e.message)});
  function perform(fn:()=>Promise<unknown>){setNotice('');work.mutate(fn);}
  function chatgptPerform(fn:()=>Promise<unknown>){perform(async()=>{try{await fn();}finally{await client.invalidateQueries({queryKey:['chatgpt']});}});}
  const welcome=chatgpt.data?.accounts.find(a=>a.active&&a.plan_usage&&a.welcome_pending);
  function doc<T>(action:string,data:Record<string,unknown>={}){return api<T>('/api/documents/'+action,{project:pid,...data});}
  async function start(action:string,payload:Record<string,unknown>){await api('/api/jobs',{project:pid,action,payload,request_key:crypto.randomUUID()});setTask('');await client.invalidateQueries({queryKey:['jobs',pid]});}
  const linked=useQuery({queryKey:['linked',pid,calc?.id],queryFn:()=>doc<{report:Upload;template:Upload}>('calculation',{calculation:calc!.id}),enabled:!!calc});
  const worksheet=useQuery({queryKey:['worksheet',pid,linked.data?.template.id],queryFn:()=>api<WorksheetData>('/api/view',{id:linked.data!.template.id}),enabled:!!linked.data});
  const inputs=useQuery({queryKey:['inputs',pid,linked.data?.report.id],queryFn:()=>api<Inspection>('/api/inspect',{id:linked.data!.report.id,template_id:linked.data!.template.id,...calc!.options}),enabled:!!linked.data&&!!calc});
  const preflight=useQuery({queryKey:['preflight',report?.id,template?.id,cases,mapping],queryFn:()=>api<Inspection>('/api/inspect',{id:report!.id,template_id:template!.id,cases,...(mapping===null?{}:{input_map:mapping})}),enabled:!!report&&!!template&&cases.length>0,retry:false,refetchOnWindowFocus:false,refetchOnReconnect:false});
  // Runs saved before projects held several calculations belong to the single `primary` calculation.
  const runs=(p?.runs||[]).filter(r=>(r.calculation_id||'primary')===calc?.id);
  const run=runs.find(r=>r.id===selectedRun)||runs.at(-1);
  const result=useQuery({queryKey:['result',pid,run?.id],queryFn:async()=> (await request(`/api/documents/${pid}/runs/${run!.id}/calculation.html`)).text(),enabled:!!run&&(view==='calculated'||tab==='outputs')});
  // The generated Mathcad file this run delivers, rendered by the same viewer as the template.
  const delivered=useQuery({queryKey:['run-file',pid,run?.id],queryFn:async()=>api<WorksheetData>('/api/view',{id:(await doc<Upload>('run-file',{run:run!.id})).id}),enabled:!!run&&tab==='outputs'&&outputView==='mathcad'});
  const facts=(p?.extractions||[]).flatMap(r=>r.facts.map(f=>({fact:f,run:r})));
  function showSource(id:string,page=1,quote=''){opener.current=document.activeElement as HTMLElement;setSource({id,page,quote});setDrawer(true);}
  function closeSources(){setDrawer(false);requestAnimationFrame(()=>{const target=opener.current?.isConnected?opener.current:sourcesButton.current;target?.focus();});}
  useEffect(()=>{if(!drawer)return;const close=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();closeSources();}};document.addEventListener('keydown',close);return()=>document.removeEventListener('keydown',close);},[drawer]);
  async function addFiles(list:FileList|File[]){
    if(!p)throw new Error('Create a project first.');
    for(const file of Array.from(list)){
      if(file.name.toLowerCase().endsWith('.pdf'))await api('/api/documents/upload?project='+pid+'&name='+encodeURIComponent(file.name),file,true);
      else if(/\.(gp11t|txt|mcdx)$/i.test(file.name)){
        // A project holds many calculations: new files start another one. Templates join the workspace library.
        if(/\.mcdx$/i.test(file.name))setTemplate(await addTemplate(file));
        else{const value=await api<Upload>('/api/upload?kind=report&name='+encodeURIComponent(file.name),file,true);setReport(value);setCases(value.inspection?.selected_cases||[]);}
        setCalcId('new');setTab('inputs');
      }else throw new Error('Choose PDF sources, a GROUP .gp11t/.txt report or a Mathcad .mcdx template.');
    }
  }
  async function addTemplate(file:File){
    const saved=await api<LibraryTemplate>('/api/templates/upload?name='+encodeURIComponent(file.name),file,true);
    await client.invalidateQueries({queryKey:['templates']});
    return libraryUpload(saved.id);
  }
  function libraryUpload(id:string){return api<Upload>('/api/templates/'+id+'/use',{});}
  function workflowAction(action:WorkflowAction){
    if(action==='add_files')files.current?.click();
    else if(action==='index_documents')perform(()=>start('ingest',{}));
    else if(action==='extract_requirements'){setTask('');setTab('answers');setAskPrompt('Extract the requirements that apply to my calculation, including values, conditions and source evidence.');}
    else if(action==='review_requirements')setTab('requirements');
    else if(action==='map_inputs')setTab('inputs');
    else if(action==='view_outputs')setTab('outputs');
    else if(action==='calculate'){if(calc)perform(()=>start('calculate',{calculation:calc.id}));else setTab('inputs');}
  }
  // Primary path: raw report + Mathcad template -> checked inputs -> calculated outputs. Ask is not a step.
  const connected=!!calc,calculating=active.some(j=>CALCULATION_ACTIONS.includes(j.action));
  // Only runs recorded as calculated count as calculated outputs; a failed or incomplete run does not.
  const calculatedRuns=runs.filter(r=>r.calculated===true).length;
  const calculateAll=()=>perform(async()=>{await start('calculate_all',{calculations:calcs.map(c=>c.id)});setTab('outputs');});
  // The latest finished Calculate all (jobs are listed newest first) and what it recorded per calculation.
  const lastAll=jobs.data?.find(j=>j.action==='calculate_all'&&j.state==='succeeded'&&!!j.result);
  const lastRows=new Map((lastAll?.result as CalculateAllResult|undefined)?.calculations.map(r=>[r.calculation_id,r])||[]);
  const [dismissed,setDismissed]=useState<string[]>([]);
  // A failure from the latest Calculate all stands until a calculated run of that calculation is saved after it.
  function calcStatus(id:string){
    const done=(p?.runs||[]).filter(r=>(r.calculation_id||'primary')===id&&r.calculated===true);
    if(lastRows.get(id)?.status==='failed'&&!done.some(r=>r.created>=(lastAll?.created||0)))return 'failed';
    return done.length?'calculated':'not calculated';
  }
  const calculate=()=>perform(async()=>{await start('calculate',{calculation:calc!.id});setTab('outputs');setView('calculated');});
  const steps=[['Raw report',connected||!!report],['Mathcad template',connected||!!template],['Inputs connected',connected],['Calculated',calculatedRuns>0]] as const;
  const current=steps.findIndex(([,done])=>!done);
  const next:{label:string;run:()=>void;disabled?:boolean}|null=!p?null
    :!connected&&!report?{label:'Add raw report',run:()=>reportFile.current?.click()}
    :!connected&&!template?{label:'Add Mathcad template',run:()=>templateFile.current?.click()}
    :!connected?(tab==='inputs'?null:{label:'Check inputs',run:()=>{setTab('inputs');setTask('');}})
    :{label:calcs.length>1?'Calculate this':'Calculate',run:calculate,disabled:calculating};
  const navGroups:[string,[Tab,string,number|undefined][]][]=[
    ['Calculation',[['inputs','Inputs',undefined],['worksheet','Worksheet',undefined],['outputs','Outputs',runs.length||undefined]]],
    ['Reference documents',[['requirements','Requirements',facts.length]]]];
  function choose(input:HTMLInputElement){if(input.files){const selected=Array.from(input.files);perform(()=>addFiles(selected));input.value='';}}
  const errors=[notice,jobs.error?.message,project.error?.message,library.error?.message,delivered.error?.message,preflight.error?.message,worksheet.error?.message,result.error?.message].filter(Boolean);
  if(session.error)return <Login onSuccess={()=>client.invalidateQueries({queryKey:['session']})} error={session.error.message}/>;
  return <div className={'app-shell '+(drawer?'source-open':'')}>
    <aside className="project-nav" aria-label="Project navigation"><a className="brand" href="/"><Icon name="sheet"/>MCDXKit</a>
      <div className="project-picker"><label htmlFor="projects">Project</label><select id="projects" value={pid} onChange={e=>setPid(e.target.value)}><option value="" disabled>Choose a project</option>{libraries.data?.projects.map(x=><option key={x.id} value={x.id}>{x.title}</option>)}</select><Button className="new-project" onPress={()=>setCreating(!creating)}><Icon name="plus"/>New project</Button></div>
      {creating&&<form className="new-project-form" onSubmit={e=>{e.preventDefault();perform(async()=>{const created=await api<Project>('/api/documents/create',{title:newTitle});setPid(created.id);setCreating(false);setNewTitle('');});}}><Field label="Project name" value={newTitle} onChange={setNewTitle}/><Button type="submit" isDisabled={!newTitle.trim()||work.isPending}>Create</Button></form>}
      <div className="nav-groups">
        {/* Ask is a separate destination, available from every view, with or without a project or calculation. */}
        <nav aria-label="Ask" className="ask-nav"><Button aria-current={tab==='answers'?'page':undefined} onPress={()=>{setTab('answers');setTask('');}}>Ask</Button></nav>
        {/* The template library is workspace-wide: available with or without a project. */}
        <nav aria-label="Templates" className="ask-nav"><Button aria-current={tab==='templates'?'page':undefined} onPress={()=>{setTab('templates');setTask('');}}>Templates{library.data?.templates?.length?<span>{library.data.templates.length}</span>:null}</Button></nav>
        {navGroups.map(([group,items])=><nav key={group} aria-label={group}><p className="nav-label" aria-hidden="true">{group}</p>
          {items.map(([id,label,count])=><Button key={id} aria-current={tab===id?'page':undefined} onPress={()=>{setTab(id);setTask('');}}>{label}{count? <span>{count}</span>:null}</Button>)}</nav>)}
      </div>
      <div className="nav-bottom"><Button onPress={()=>setTask(task==='settings'?'':'settings')}><Icon name="settings"/>Services</Button><a href="/convert">Batch conversion</a><p>Work saved on this device</p></div>
    </aside>
    <div className="workspace-main"><header className="workspace-header"><div><h1>{p?.title||'Your engineering workspace'}</h1><p>{!p?'Raw report + Mathcad template → checked inputs → calculated outputs.':calc?`${calc.name} · ${calcs.length} calculation${calcs.length===1?'':'s'} in this project`:calcs.length?'New calculation':'No calculation connected'}</p></div><div className="header-actions"><Button ref={sourcesButton} aria-label="Sources" aria-expanded={drawer} onPress={()=>{if(drawer)closeSources();else{opener.current=sourcesButton.current;setDrawer(true);}}}><Icon name="sources"/>Sources{p?.sources.length?<span className="count">{p.sources.length}</span>:null}</Button>{p&&calcs.length>1&&<Button isDisabled={calculating||work.isPending} onPress={calculateAll}>Calculate all ({calcs.length})</Button>}{next&&<Button className="primary" isDisabled={next.disabled||work.isPending} onPress={next.run}>{next.label}</Button>}</div></header>
      <div className="task-bar"><div className="task-actions">{p&&calcs.length>0&&<label className="calc-picker">Calculation<select aria-label="Calculation" value={calc?.id||'new'} onChange={e=>{setCalcId(e.target.value);setSelectedRun('');if(e.target.value==='new')setTab('inputs');}}>{calcs.map(c=><option key={c.id} value={c.id}>{c.name} · {calcStatus(c.id)}</option>)}<option value="new">+ New calculation</option></select></label>}<Button isDisabled={!p} onPress={()=>files.current?.click()}><Icon name="plus"/>Add files</Button><Button isDisabled={!p?.sources.length} onPress={()=>workflowAction('extract_requirements')}>Extract requirements</Button></div>
        {p&&<ol className="flow-steps" aria-label="Calculation steps">{steps.map(([label,done],i)=><li key={label} className={done?'done':i===current?'current':''} aria-current={i===current?'step':undefined}><span aria-hidden="true">{done?'✓':i+1}</span>{label}{done&&<span className="sr-only">, done</span>}</li>)}</ol>}</div>
      <input ref={files} type="file" multiple accept=".pdf,.txt,.gp11t,.mcdx" hidden onChange={e=>choose(e.target)}/>
      <input ref={reportFile} type="file" accept=".gp11t,.txt" hidden onChange={e=>choose(e.target)}/>
      <input ref={templateFile} type="file" accept=".mcdx" hidden onChange={e=>choose(e.target)}/>
      {errors.map((message,i)=><div key={i} className="notice" role="alert">{message}<Button onPress={()=>setNotice('')} aria-label="Dismiss message"><Icon name="close"/></Button></div>)}
      {libraries.error&&<p role="alert">{libraries.error.message}</p>}
      {jobs.data?.slice(0,1).filter(j=>j.state==='failed'&&j.action!=='ask').map(job=><JobStatus key={job.id} job={job} onCancel={()=>{}}/>)}
      {active.filter(j=>j.action!=='ask').map(job=><JobStatus key={job.id} job={job} onCancel={()=>perform(()=>api('/api/jobs/'+job.id+'/cancel',{}))}/>)}
      {welcome&&<ChatGPTWelcome onDone={()=>chatgptPerform(()=>api('/api/chatgpt/acknowledge',{account:welcome.id}))}/>}
      {task==='settings'&&<Settings config={libraries.data?.configuration||{}} chatgpt={chatgpt.data} chatgptPerform={chatgptPerform} returnTo={{project:pid||undefined,view:tab==='answers'?'ask':tab}} onClose={()=>{returning.current=false;setTask('');}} perform={perform} pending={work.isPending}/>}
      {task==='extract'&&p&&<QuestionTask key={task} kind={task} sources={p.sources} pending={work.isPending} close={()=>setTask('')} submit={(payload)=>perform(async()=>{await start(task,payload);setTab('requirements');})}/>}
      {reviewFact&&<ReviewTask fact={reviewFact} close={()=>setReviewFact(null)} pending={work.isPending} submit={(status,reviewer,note)=>perform(async()=>{await doc('review-requirement',{requirement:reviewFact.id,status,reviewer,note});setReviewFact(null);})}/>}
      {task==='basis'&&p&&calc&&<BasisTask project={p} calculation={calc} ids={selection} close={()=>setTask('')} pending={work.isPending} submit={(data)=>perform(async()=>{const d=await doc<Draft>('requirement-draft',{...data,calculation:calc.id});await start('build',{draft:d.id});setSelection([]);setTab('outputs');setView('calculated');})}/>}
      <main className={'work-surface '+tab} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();perform(()=>addFiles(e.dataTransfer.files));}}>
        {tab==='templates'?<TemplatesPanel templates={library.data?.templates} loading={library.isLoading} pending={work.isPending} canUse={!!p}
            upload={files=>perform(async()=>{for(const f of files){if(!/\.mcdx$/i.test(f.name))throw new Error('Templates are Mathcad .mcdx files.');await api('/api/templates/upload?name='+encodeURIComponent(f.name),f,true);}await client.invalidateQueries({queryKey:['templates']});})}
            open={id=>libraryUpload(id)} archive={id=>perform(async()=>{await api('/api/templates/'+id+'/archive',{});await client.invalidateQueries({queryKey:['templates']});})}
            use={id=>perform(async()=>{setTemplate(await libraryUpload(id));setCalcId('new');setTab('inputs');})} createProject={()=>setCreating(true)}/>
        :!p?(pid&&project.isLoading)||libraries.isLoading?<p role="status" className="empty-inline">Opening workspace…</p>
          :tab==='answers'?<div className="empty-workspace"><Icon name="sources"/><h2>Ask, your project assistant.</h2><p>Ask works alongside the calculation, not as a step in it. It can guide you through the workflow at any point, and when a project has indexed reference documents it answers questions with citations that open the original page. It never changes calculation inputs.</p><Button className="primary" onPress={()=>setCreating(true)}>Create a project</Button><p className="muted">Conversations are kept with a project. Ask stays in the sidebar before, during and after calculation.</p></div>
          :<div className="empty-workspace"><Icon name="sheet"/><h2>Calculate from a raw report and a Mathcad template.</h2><ol className="start-steps"><li>Add the raw load report <small>.gp11t or .txt</small></li><li>Add the Mathcad template <small>.mcdx</small></li><li>Check load cases and input mapping</li><li>Calculate and download the outputs</li></ol><Button className="primary" onPress={()=>setCreating(true)}>Create a project</Button><p className="muted">A project keeps both original files and every calculated run. Reference PDFs are optional; they support Ask.</p></div>:<>
          {tab==='worksheet'&&(calc?<><div className="section-toolbar"><h2>Worksheet</h2><div className="segmented"><Button aria-pressed={view==='template'} onPress={()=>setView('template')}>Template</Button><Button aria-pressed={view==='calculated'} isDisabled={!run} onPress={()=>setView('calculated')}>Calculated</Button></div></div>{view==='calculated'&&result.data?<><p className="file-note">Calculated by CalcpadCE. Prime-native execution and engineering approval remain unverified.</p><Calculated html={result.data}/></>:worksheet.data?<Worksheet data={worksheet.data}/>:<p role="status">Loading the complete template…</p>}</>:<div className="empty-workspace"><Icon name="sheet"/><h2>No calculation connected yet.</h2><p>Add the raw load report and the Mathcad template in Inputs, check the mapping, then connect. The template appears here once connected.</p><Button className="primary" onPress={()=>setTab('inputs')}>Go to inputs</Button><p className="muted">Drop files anywhere in this workspace.</p></div>)}
          {tab==='inputs'&&<InputPanel project={p} calculation={calc} templates={library.data?.templates||[]} pickTemplate={id=>perform(async()=>setTemplate(await libraryUpload(id)))} inspection={inputs.data} report={report} template={template} preflight={preflight.data} checking={preflight.isFetching} checkFailed={preflight.isError} recheck={()=>void preflight.refetch()} cases={cases} mapping={mapping} setCases={setCases} setMapping={setMapping} addReport={()=>reportFile.current?.click()} addTemplate={()=>templateFile.current?.click()} pending={work.isPending} connect={()=>perform(async()=>{const linkedNow=await doc<{calculation:Calculation}>('connect',{id:report!.id,...(template!.template_id?{library_template:template!.template_id}:{template_id:template!.id}),cases,...(mapping===null?{}:{input_map:mapping})});setReport(null);setTemplate(null);setMapping(null);setCalcId(linkedNow.calculation.id);setTab('worksheet');})}/>}
          {tab==='requirements'&&<><div className="section-toolbar"><div><h2>Requirements</h2><p>Values, conditions and the evidence behind them.</p></div><Button className="primary" isDisabled={!selection.length||!calc} onPress={()=>setTask('basis')}>Use reviewed selection</Button></div>{!facts.length&&<div className="empty-inline"><h3>No requirements extracted yet.</h3><p>Select a source and a few pages to find values with their surrounding conditions.</p><Button isDisabled={!p.sources.length} onPress={()=>workflowAction('extract_requirements')}>Extract requirements</Button></div>}{facts.map(({fact,run:r})=><RequirementRow key={fact.id} fact={fact} source={{id:r.source_id,name:r.source_name}} selected={selection.includes(fact.id)} onSelect={()=>setSelection(selection.includes(fact.id)?selection.filter(id=>id!==fact.id):[...selection,fact.id])} onSource={showSource} onReview={()=>setReviewFact(fact)}/>)}{p.extractions?.map(r=>r.gaps.length>0&&<details key={r.id} className="gaps"><summary>Extraction limitations · {r.source_name}</summary><ul>{r.gaps.map((g,i)=><li key={i}>{g}</li>)}</ul><p>Model: {r.model}</p></details>)}</>}
          {tab==='answers'&&<div className="ask-shell"><p className="ask-context">{connected
            ?<>Project <strong>{p.title}</strong> · calculation {calc!.name}{calc!.options.cases.length?` · cases ${calc!.options.cases.join(', ')}`:''} · {calculatedRuns} calculated {calculatedRuns===1?'run':'runs'}{calcs.length>1?` · ${calcs.length} calculations in project`:''}</>
            :<>Project <strong>{p.title}</strong> · no calculation connected. Ask doesn’t need one.</>}</p><Ask key={pid} initialQuestion={askPrompt} plan={libraries.data?.configuration.provider==='chatgpt'} onServices={()=>setTask('settings')} onWorkflow={workflowAction} project={p} jobs={jobs.data||[]} onSend={(question,parent)=>start('ask',{question,parent})} onSaveOffer={answer=>perform(()=>start('extract_offer',{answer}))} onStop={id=>perform(()=>api('/api/jobs/'+id+'/cancel',{}))} onSource={showSource} onSources={()=>{opener.current=document.activeElement as HTMLElement;setDrawer(true);}}/></div>}
          {tab==='outputs'&&<><div className="section-toolbar"><div><h2>Calculated outputs</h2><p>Equations run by the shared engine. Each run preserves its inputs.</p></div>{run&&<select aria-label="Calculation run" value={run.id} onChange={e=>setSelectedRun(e.target.value)}>{[...runs].reverse().map((r,i)=><option key={r.id} value={r.id}>{new Date(r.created*1000).toLocaleString()} {i===0?'· Latest':''}</option>)}</select>}</div>{lastAll&&!dismissed.includes(lastAll.id)&&<CalculateAllSummary job={lastAll} current={calc?.id} onSelect={id=>{setCalcId(id);setSelectedRun('');}} onDismiss={()=>setDismissed([...dismissed,lastAll.id])}/>}{!run?<p className="empty-inline">{calc?'Calculate this calculation to see its outputs.':'Connect a report and template, then calculate.'} Finished outputs remain available after closing the browser.</p>:<><div className="output-strip"><span>CalcpadCE calculated</span><span>{run.check_summary?.total?`${run.check_summary.passed} / ${run.check_summary.total} template checks passed`:'No template check summary'}</span><span>Prime execution unverified</span></div><div className="output-views"><div className="segmented" aria-label="Output view"><Button aria-pressed={outputView==='report'} onPress={()=>setOutputView('report')}>Calculated report</Button><Button aria-pressed={outputView==='mathcad'} onPress={()=>setOutputView('mathcad')}>Mathcad file</Button></div><div className="download-bar">{[['calculation.mcdx','Download Mathcad file'],['calculation.cpd','Download Calcpad file'],['package.zip','Download package']].map(([name,label])=><Button key={name} onPress={()=>perform(()=>download(`/api/documents/${pid}/runs/${run.id}/${name}`,name))}>{label}</Button>)}</div></div>
            {outputView==='mathcad'?delivered.data?<Worksheet data={delivered.data} note="This is calculation.mcdx, the Mathcad file you download. It adds the report's load summary as live Mathcad formulas, and each mapped template input refers to that summary instead of a typed value; unmapped inputs keep their template values. Stale results are removed, so Mathcad Prime recalculates it when opened."/>:delivered.isError?null:<p role="status">Opening the Mathcad file…</p>
            :result.data?<Calculated html={result.data}/>:<p role="status">Opening calculated worksheet…</p>}</>}{jobs.data?.filter(j=>CALCULATION_ACTIONS.includes(j.action)&&(j.state==='failed'||j.state==='cancelled')).slice(0,4).map(j=><JobStatus key={j.id} job={j} onCancel={()=>{}}/>)}</>}
        </>}
      </main>
      <footer className="workspace-footer"><span>{work.isPending?'Saving…':active.length?`${active.length} active task${active.length===1?'':'s'}`:'Saved work stays in this workspace'}</span><span>Engineering review is a separate decision</span></footer>
    </div>
    {drawer&&<aside className="source-drawer" aria-label="Source documents"><header><h2>Sources</h2><Button aria-label="Close sources" onPress={closeSources}><Icon name="close"/></Button></header><p className="drawer-note">Original documents. Citations open the exact PDF page.</p><details className="source-picker" open={!source}><summary>Project documents · {p?.sources.length||0}</summary><div className="source-list">{p?.sources.map(s=><Button key={s.id} aria-pressed={source?.id===s.id} onPress={()=>setSource({id:s.id,page:1,quote:''})}><span>{s.name}</span><small>{s.status}</small></Button>)}{!p?.sources.length&&<p>No sources yet.</p>}</div></details><div className="drawer-actions"><Button isDisabled={!p?.sources.length} onPress={()=>{setTask('extract');closeSources();}}>Choose specific pages…</Button><Button isDisabled={!p} onPress={()=>files.current?.click()}>Add PDFs</Button><Button isDisabled={!p?.sources.length||work.isPending||active.some(j=>['ingest','index_status'].includes(j.action))} onPress={()=>perform(()=>start('ingest',{}))}>Index documents</Button><Button isDisabled={!p?.sources.length||work.isPending||active.some(j=>['ingest','index_status'].includes(j.action))} onPress={()=>perform(()=>start('index_status',{}))}>Refresh index</Button></div>{source&&<><div className="source-title"><strong>{p?.sources.find(s=>s.id===source.id)?.name}</strong><Button onPress={()=>perform(()=>download(`/api/documents/${pid}/sources/${source.id}`,p?.sources.find(s=>s.id===source.id)?.name||'source.pdf'))}>Download original</Button></div>{source.quote&&<blockquote>{source.quote}</blockquote>}<PDFPage url={`/api/documents/${pid}/sources/${source.id}`} initialPage={source.page}/></>}</aside>}
  </div>;
}

function Login({error,onSuccess}:{error:string;onSuccess:()=>void}) {
  const [key,setKey]=useState(''),[message,setMessage]=useState(error);
  return <main className="login"><h1>MCDXKit</h1><p>{message}</p><form onSubmit={async e=>{e.preventDefault();try{await api('/api/login',{access_token:key});setKey('');onSuccess();}catch(e){setMessage((e as Error).message);}}}><Field label="Server access token" value={key} onChange={setKey} type="password"/><Button type="submit">Sign in</Button></form></main>;
}

function QuestionTask({kind,sources,pending,close,submit}:{kind:string;sources:Source[];pending:boolean;close:()=>void;submit:(payload:Record<string,unknown>)=>void}) {
  const [question,setQuestion]=useState(''),[source,setSource]=useState(sources[0]?.id||''),[pages,setPages]=useState('');
  return <form className="task-panel" onSubmit={e=>{e.preventDefault();submit(kind==='ask'?{question}:{source,pages,question});}}><div className="section-toolbar"><h2>{kind==='ask'?'Ask your documents':'Extract requirements'}</h2><Button onPress={close} aria-label="Close task"><Icon name="close"/></Button></div>{kind==='extract'&&<div className="form-grid"><label>Source<select value={source} onChange={e=>setSource(e.target.value)}>{sources.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label><Field label="PDF pages · e.g. 131-132, 147" value={pages} onChange={setPages}/></div>}<Field label={kind==='ask'?'Question':'What values and conditions do you need?'} value={question} onChange={setQuestion} area/><div className="form-end"><p>{kind==='extract'?'Selected text pages go to DeepSeek 4.1 Flash through OpenRouter / DeepInfra. No indexing required. Results require review.':'Indexed passages go to your configured answer model. Answers include source citations.'}</p><Button className="primary" type="submit" isDisabled={pending||!question.trim()||(kind==='extract'&&!pages.trim())}>{kind==='ask'?'Ask documents':'Extract selected pages'}</Button></div></form>;
}

function ReviewTask({fact,close,pending,submit}:{fact:Fact;close:()=>void;pending:boolean;submit:(status:string,reviewer:string,note:string)=>void}) {
  const [reviewer,setReviewer]=useState(''),[note,setNote]=useState(''),[status,setStatus]=useState('reviewed');
  return <form className="task-panel" onSubmit={e=>{e.preventDefault();submit(status,reviewer,note);}}><div className="section-toolbar"><h2>Review {fact.property}</h2><Button onPress={close}>Close</Button></div><p>{fact.subject} · <FactValue fact={fact}/>. Check the original page and every surrounding condition.</p><div className="form-grid"><Field label="Reviewer" value={reviewer} onChange={setReviewer}/><label>Decision<select value={status} onChange={e=>setStatus(e.target.value)}><option value="reviewed">Evidence reviewed</option><option value="rejected">Reject requirement</option></select></label></div><Field label="Review note" value={note} onChange={setNote} area/><div className="form-end"><p>This records your review; it does not certify the design.</p><Button type="submit" className="primary" isDisabled={pending||!reviewer.trim()||!note.trim()}>Record review</Button></div></form>;
}

function BasisTask({project,calculation,ids,close,pending,submit}:{project:Project;calculation:Calculation;ids:string[];close:()=>void;pending:boolean;submit:(data:Record<string,unknown>)=>void}) {
  const [reviewer,setReviewer]=useState(''),[note,setNote]=useState(''),[applicability,setApplicability]=useState(''),[bindings,setBindings]=useState<Record<string,{variable:string;value:string}>>({});
  const selected=project.extractions?.flatMap(r=>r.facts).filter(f=>ids.includes(f.id))||[];
  const available=calculation.template_inputs.inputs.filter(i=>!i.component&&i.status!=='ambiguous');
  return <form className="task-panel" onSubmit={e=>{e.preventDefault();submit({requirements:ids,reviewer,note,applicability,bindings:selected.filter(f=>bindings[f.id]?.variable).map(f=>({requirement:f.id,variable:bindings[f.id].variable,value:Number(bindings[f.id].value),unit:available.find(i=>i.variable===bindings[f.id].variable)!.unit}))});}}><div className="section-toolbar"><h2>Connect requirements to {calculation.name}</h2><Button onPress={close}>Close</Button></div><p>Retain each item as design-basis evidence, or explicitly map it to a compatible template input. Raw loads remain sourced from the report.</p>{selected.map(f=><div className="binding-row" key={f.id}><span>{f.property} · <FactValue fact={f}/><small>{f.review?.status==='reviewed'?'Review recorded':'Review required before calculation'}</small></span><label>Use<select value={bindings[f.id]?.variable||''} onChange={e=>setBindings({...bindings,[f.id]:{variable:e.target.value,value:''}})}><option value="">Design-basis evidence only</option>{available.filter(i=>i.unit===f.unit).map(i=><option key={i.variable} value={i.variable}>{i.variable} · {i.unit}</option>)}</select></label>{bindings[f.id]?.variable&&<label>Accepted value<input type="number" step="any" required value={bindings[f.id].value} onChange={e=>setBindings({...bindings,[f.id]:{...bindings[f.id],value:e.target.value}})}/></label>}</div>)}<Field label="Why do these requirements apply to this structure?" value={applicability} onChange={setApplicability} area/><div className="form-grid"><Field label="Reviewer" value={reviewer} onChange={setReviewer}/><Field label="Input decision note" value={note} onChange={setNote}/></div><div className="form-end"><p>Creates a reviewed basis and runs the existing template equations.</p><Button className="primary" type="submit" isDisabled={pending||selected.some(f=>f.review?.status!=='reviewed')||!reviewer.trim()||!note.trim()||!applicability.trim()}>Save basis & calculate</Button></div></form>;
}

function InputPanel({project,calculation,templates,pickTemplate,inspection,report,template,preflight,checking,checkFailed,recheck,cases,mapping,setCases,setMapping,addReport,addTemplate,pending,connect}:{project:Project;inspection?:Inspection;report:Upload|null;template:Upload|null;preflight?:Inspection;checking:boolean;checkFailed:boolean;recheck:()=>void;cases:number[];mapping:Record<string,string>|null;setCases:(v:number[])=>void;setMapping:(v:Record<string,string>)=>void;addReport:()=>void;addTemplate:()=>void;pending:boolean;connect:()=>void;calculation?:Calculation;templates:LibraryTemplate[];pickTemplate:(id:string)=>void}) {
  const data=calculation?inspection:preflight, inputs=data?.template_inputs?.inputs||calculation?.template_inputs.inputs||[];
  const [query,setQuery]=useState(''),shown=inputs.filter(i=>matchesInput(i,query));
  // Governing case for a mapped value, shown only when it is the value being written.
  const governing=(component:string|null|undefined,value:number)=>{const g=component?data?.governing?.[component]:undefined;
    return g&&g.value===value?<small className="provenance">Case {g.case} · {g.case_name}</small>:null;};
  const map=mapping||preflight?.template_inputs?.input_map||{};
  const open=!calculation,status=preflight?.template_validation?.status;
  // Connect needs a finished, successful check of the current selection; cached results never stand in for one
  // that is running or failed. A failed check is retried only when the engineer asks.
  const current=!checking&&!checkFailed&&status==='ready';
  const state=!cases.length?'Select at least one load case.':checking?'Checking the template against the report…'
    :checkFailed?'The input check failed, so nothing can be connected. Results shown below may be from an earlier check.'
    :status==='ready'?'Inputs are ready. Connect to keep both files and calculate.':status?'Resolve the items below before connecting.':'The input check has not run.';
  const slot=(kind:string,noun:string,name:string|undefined,hint:string,add:()=>void,extra?:React.ReactNode)=><div className={name?'filled':''}><span>{kind}</span><strong>{name||hint}</strong>
    {open&&<div className="slot-actions"><Button onPress={add} isDisabled={pending} aria-label={(name?'Replace ':'Choose ')+noun}>{name?'Replace':'Choose file'}</Button>{extra}</div>}</div>;
  const fromLibrary=templates.length>0&&<select aria-label="Template from library" value={template?.template_id||''} disabled={pending} onChange={e=>{if(e.target.value)pickTemplate(e.target.value);}}><option value="">From template library…</option>{templates.map(t=><option key={t.id} value={t.id}>{t.name}{t.version>1?` · v${t.version}`:''}</option>)}</select>;
  return <><div className="section-toolbar"><div><h2>{calculation?calculation.name:project.calculations.length?'New calculation':'Calculation inputs'}</h2><p>{open?'Add a raw load report and pick a Mathcad template, then confirm load cases and input mapping. A project can hold many calculations.':'Immutable source files and mapped values for this calculation.'}</p></div></div>
    <div className="input-files">{slot('Raw report','raw report',calculation?.report_name||report?.name,'.gp11t or .txt',addReport)}{slot('Mathcad template','Mathcad template',calculation?.template_name||template?.name,'.mcdx',addTemplate,fromLibrary)}</div>
    {open&&report&&template&&<div className="check-state"><p className="file-note" role="status">{state}</p>
      {cases.length>0&&!checking&&(checkFailed||!status)&&<Button onPress={recheck}>Check again</Button>}</div>}{open&&report&&<fieldset className="case-list"><legend>Load cases</legend>{report.inspection?.cases.map(c=><Checkbox key={c.id} isSelected={cases.includes(c.id)} onChange={on=>setCases(on?[...cases,c.id]:cases.filter(i=>i!==c.id))}><span className="check-mark"/>{c.id} · {c.name}</Checkbox>)}</fieldset>}{calculation&&<p className="file-note">Selected cases: {calculation.options.cases.join(', ')}.</p>}{(calculation||report)&&<p className="file-note">Each mapped value is that component's own extreme over the selected cases, with its governing case shown. Extremes from different cases are not a concurrent load combination.</p>}{inputs.length>0&&<InputSearch value={query} onChange={setQuery} shown={shown.length} total={inputs.length}/>}{inputs.length>0&&<div className="table-scroll"><table><thead><tr><th>Template input</th><th>Template value</th><th>Raw source</th><th>Mapped value</th></tr></thead><tbody>{!shown.length&&<tr><td colSpan={4} className="empty-inline">No inputs match “{query.trim()}”.</td></tr>}{shown.map(i=><tr key={i.variable}><td><strong>{i.variable}</strong><small>{i.expression}</small></td><td className="numeric">{i.value} <span>{unitText(i.unit)}</span></td><td>{open?<select aria-label={'Load source for '+i.variable} value={map[i.variable]||''} disabled={i.status==='ambiguous'} onChange={e=>{const next={...map};if(e.target.value)next[i.variable]=e.target.value;else delete next[i.variable];setMapping(next);}}>{[['','Keep template value'],['P','Axial P'],['Vy','Shear y'],['Vz','Shear z'],['My','Moment y'],['Mz','Moment z']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>:i.component||'Template retained'}</td><td className="numeric">{i.replacement?<>{i.replacement.value} <span>{unitText(i.replacement.unit)}</span>{governing(i.component,i.replacement.value)}</>:'Retained'}</td></tr>)}</tbody></table></div>}{data?.template_validation?.errors.map((e,i)=><p key={i} role="alert">{e}</p>)}{data?.template_validation?.warnings.map((e,i)=><p key={i} className="file-note">{e}</p>)}{open&&<div className="form-end"><p>Connecting keeps an immutable copy of both files as a new calculation in this project. Existing calculations and their outputs are unchanged.</p><Button className="primary" onPress={connect} isDisabled={pending||!cases.length||!current}>Connect calculation</Button></div>}</>;
}

function TemplatesPanel({templates,loading,pending,canUse,upload,open,archive,use,createProject}:{templates?:LibraryTemplate[];loading:boolean;pending:boolean;canUse:boolean;upload:(files:File[])=>void;open:(id:string)=>Promise<Upload>;archive:(id:string)=>void;use:(id:string)=>void;createProject:()=>void}) {
  const [selected,setSelected]=useState(''),picker=useRef<HTMLInputElement>(null);
  const chosen=templates?.find(t=>t.id===selected)||templates?.[0];
  const [query,setQuery]=useState(''),shown=(chosen?.inputs||[]).filter(i=>matchesInput(i,query));
  // The stored template is registered for the same read-only worksheet viewer the calculation uses.
  const sheet=useQuery({queryKey:['template-sheet',chosen?.id],queryFn:async()=>api<WorksheetData>('/api/view',{id:(await open(chosen!.id)).id}),enabled:!!chosen});
  return <><div className="section-toolbar"><div><h2>Template library</h2><p>Every Mathcad template in this workspace, reusable by any project. Uploading never overwrites: a changed file becomes a new version.</p></div><Button className="primary" isDisabled={pending} onPress={()=>picker.current?.click()}><Icon name="plus"/>Upload templates</Button></div>
    <input ref={picker} type="file" accept=".mcdx" multiple hidden onChange={e=>{if(e.target.files){upload(Array.from(e.target.files));e.target.value='';}}}/>
    {loading?<p role="status" className="empty-inline">Loading templates…</p>:!templates?.length?<div className="empty-inline"><h3>No templates yet.</h3><p>Upload your Mathcad .mcdx templates once; every project can then pick from them.</p></div>
    :<div className="template-library"><ul className="template-list" aria-label="Templates">{templates.map(t=><li key={t.id}><Button aria-pressed={chosen?.id===t.id} onPress={()=>{setSelected(t.id);setQuery('');}}><strong>{t.name}</strong><small>{t.version>1?`Version ${t.version} · `:''}{t.inputs.length} inputs · {t.calculations} calculation{t.calculations===1?'':'s'}</small></Button></li>)}</ul>
      {chosen&&<section className="template-detail" aria-label={'Template '+chosen.name}><div className="section-toolbar"><div><h3>{chosen.name}</h3><p>Added {new Date(chosen.created*1000).toLocaleDateString()} · SHA-256 {chosen.sha256.slice(0,12)}… · {chosen.mapping_basis||'no load mapping preset; map inputs explicitly'}</p></div><div className="row-actions">{canUse?<Button className="primary" isDisabled={pending} onPress={()=>use(chosen.id)}>Use in new calculation</Button>:<Button onPress={createProject}>Create a project to use it</Button>}<Button isDisabled={pending} onPress={()=>{archive(chosen.id);setSelected('');}}>Archive</Button></div></div>
        {/* Inputs and the worksheet side by side, each scrolling on its own, so a long input list never pushes the sheet out of view. */}
        <div className="template-panes"><section className="template-pane" aria-label="Template inputs"><h4 className="template-pane-title">Inputs · {chosen.inputs.length}</h4><InputSearch value={query} onChange={setQuery} shown={shown.length} total={chosen.inputs.length}/>
        <div className="table-scroll"><table><thead><tr><th>Input</th><th>Template value</th><th>Load preset</th></tr></thead><tbody>{!shown.length&&<tr><td colSpan={3} className="empty-inline">No inputs match “{query.trim()}”.</td></tr>}{shown.map(i=><tr key={i.variable}><td><strong>{i.variable}</strong><small>{i.expression}</small></td><td className="numeric">{i.value} <span>{unitText(i.unit)}</span></td><td>{i.component||(i.status==='ambiguous'?'Defined more than once':'Template value')}</td></tr>)}</tbody></table></div>
        {chosen.missing_load_inputs.length>0&&<p className="file-note">Not found in this template: {chosen.missing_load_inputs.join(', ')}. Map custom inputs explicitly when you use it.</p>}</section>
        <section className="template-pane" aria-label="Template worksheet"><h4 className="template-pane-title">Worksheet</h4>{sheet.data?<Worksheet data={sheet.data}/>:sheet.error?<p role="alert">{sheet.error.message}</p>:<p role="status">Loading worksheet…</p>}</section></div></section>}</div>}</>;
}

function Settings({config,chatgpt,chatgptPerform,returnTo,onClose,perform,pending}:{config:Configuration;chatgpt?:ChatGPTStatus;chatgptPerform:(fn:()=>Promise<unknown>)=>void;returnTo:ReturnTo;onClose:()=>void;perform:(fn:()=>Promise<unknown>)=>void;pending:boolean}) {
  const [key,setKey]=useState(''),[model,setModel]=useState(config.model||''),[models,setModels]=useState<{id:string}[]>([]),[message,setMessage]=useState('');
  return <section className="task-panel"><div className="section-toolbar"><h2>Document services</h2><Button onPress={onClose}>Close</Button></div><p>Calculation works locally. Document questions use RAGFlow search and your selected Ask provider: your ChatGPT plan or an OpenRouter free model. Selected-page extraction from Requirements uses DeepSeek 4.1 Flash.</p>
    <ChatGPTSettings key={config.provider===undefined?'loading':'ready'} status={chatgpt} provider={config.provider||'openrouter'} model={config.chatgpt_model||''} pending={pending} perform={chatgptPerform} returnTo={returnTo}/>
    <h3>OpenRouter</h3><Field label={config.router_key_saved?'OpenRouter key · saved (leave blank to keep)':'OpenRouter key'} value={key} onChange={setKey} type="password"/><div className="row-actions"><Button isDisabled={pending} onPress={()=>perform(async()=>{const response=await api<{models:{id:string}[]}>('/api/documents/models',{router_key:key});setModels(response.models);setMessage('Supported free answer models loaded.');})}>Load answer models</Button><Button isDisabled={pending} onPress={()=>perform(async()=>{await api('/api/documents/discover',{});setMessage('Local service discovery finished.');})}>Detect local RAGFlow</Button><Button isDisabled={pending} onPress={()=>perform(async()=>{await api('/api/documents/start-service',{});setMessage('Local document services are starting.');})}>Start document services</Button></div><label>Answer model<select value={model} onChange={e=>setModel(e.target.value)}><option value={config.model||''}>{config.model||'Choose a model'}</option>{models.map(m=><option key={m.id} value={m.id}>{m.id}</option>)}</select></label><div className="form-end"><p>{message||'Keys stay on the server and are never included in exported documents.'}</p><Button className="primary" isDisabled={pending} onPress={()=>perform(async()=>{await api('/api/documents/configure',{router_key:key,model});setKey('');setMessage('Connection saved.');})}>Save connection</Button></div></section>;
}

/** Template units arrive as `kip·in`, mapped values as `kip-in`, and dimensionless values as `1`. */
export function unitText(unit:string){return unit==='1'?'':unit.replace(/-/g,'·');}
