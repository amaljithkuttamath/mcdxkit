import {useEffect, useId, useMemo, useRef, useState} from 'react';
import type {Ref} from 'react';
import {Button, Dialog, DialogTrigger, Heading, Label, ListBox, ListBoxItem, Popover, TextArea, TextField} from 'react-aria-components';
import type {Selection} from 'react-aria-components';
import {download} from './api';
import {ManageUsage} from './ChatGPT';
import {Prose, citationIndex, inlineCitationIds, pagesOf, quoteOf} from './citations';
import type {CitationIndex, SourceHandler} from './citations';
import {Glyph, ModelPicker, ModelUsed} from './ModelPicker';
import type {Activity, ActivityType, Draft, Evidence, Grounding, GroundingStatement, Job, Project, WorkflowAction} from './types';
import './chat.css';

type AskProps={project:Project;jobs:Job[];initialQuestion?:string;plan?:boolean;onSend:(question:string,parent:string|null)=>Promise<void>;onStop:(id:string)=>void;onSource:SourceHandler;onSources:()=>void;onWorkflow:(action:WorkflowAction)=>void;onSaveOffer?:(answer:string)=>void;onServices?:()=>void};
type Conversation={key:string;first:Draft;count:number;last:number};
type Waiting={count:number;parent:string|null;question:string;previousJob?:string;view:string};

const BUSY=['queued','running'];
const keyOf=(a:Draft)=>a.conversation_id||a.id;
// A first question has no conversation yet; it gets its own view so "New chat" can open a separate empty draft.
const pendingKey=(job:Job)=>'job:'+job.id;
// Which conversation a job belongs to: its parent's conversation, its pending view for a first question, null if unknown.
function jobConversation(job:Job|undefined,answers:Draft[],fallback:string|null):string|null {
  if(!job)return null;
  if(job.parent===undefined)return fallback;
  if(job.parent===null)return pendingKey(job);
  const parent=answers.find(a=>a.id===job.parent);
  return parent?keyOf(parent):null;
}
const PROMPTS=['What settlement limits apply, and under what conditions?','What must the design submission include?','Do any documents disagree about the requirements?'];

export function Ask({project,jobs,initialQuestion,plan=false,onSend,onStop,onSource,onSources,onWorkflow,onSaveOffer,onServices}:AskProps) {
  const answers=project.answers;
  const conversations=useMemo(()=>{
    const map=new Map<string,Conversation>();
    answers.forEach((a,i)=>{const key=keyOf(a),c=map.get(key);if(c){c.count++;c.last=i;}else map.set(key,{key,first:a,count:1,last:i});});
    return [...map.values()].sort((x,y)=>y.last-x.last);
  },[answers]);
  const current=jobs.find(j=>j.action==='ask');
  const running=!!current&&BUSY.includes(current.state);
  const [view,setView]=useState(()=>(running&&jobConversation(current,answers,null))||conversations[0]?.key||'new');
  const [question,setQuestion]=useState(''),[submitting,setSubmitting]=useState(false),[error,setError]=useState('');
  const [waiting,setWaiting]=useState<Waiting|null>(null),[dismissed,setDismissed]=useState<string[]>([]),[stepsOpen,setStepsOpen]=useState(false),[configuring,setConfiguring]=useState(false);
  const composer=useRef<HTMLTextAreaElement>(null),end=useRef<HTMLDivElement>(null),lastTurn=useRef<HTMLElement>(null),seen=useRef({view:'',length:0});
  const stepsId=useId();
  useEffect(()=>{if(initialQuestion){setQuestion(initialQuestion);composer.current?.focus();}},[initialQuestion]);

  const thread=view==='new'||view.startsWith('job:')?[]:answers.filter(a=>keyOf(a)===view);
  const ready=project.sources.filter(s=>s.status==='ready').length;
  // Until the new job appears, the latest job is the previous request; never show its outcome as this one's.
  const starting=!!waiting&&(!current||current.id===waiting.previousJob);
  const job=starting?undefined:current;
  const live=!!job&&BUSY.includes(job.state);
  const jobView=jobConversation(job,answers,waiting?.view??conversations[0]?.key??'new');
  const jobHere=!!job&&jobView===view&&!dismissed.includes(job.id);
  const atTail=!job?false:job.parent?thread.at(-1)?.id===job.parent:job.parent===null?!thread.length:!!waiting;
  const outcome=jobHere&&atTail&&(job!.state==='failed'||job!.state==='cancelled')?job:undefined;
  const asked=waiting&&waiting.view===view?waiting.question:jobHere&&(live||outcome)?job!.request:undefined;
  const elsewhere=running&&!starting&&!jobHere;
  // A model change in progress also blocks sending, so no question goes out with the previous model.
  const blocked=running||submitting||starting||configuring;

  // Once a first question's job exists, its question moves from the draft view to that job's own view.
  useEffect(()=>{
    if(!waiting||!job||waiting.parent!==null||job.parent!==null)return;
    const key=pendingKey(job);
    if(waiting.view===key)return;
    setView(v=>v===waiting.view?key:v);
    setWaiting({...waiting,view:key});
  },[waiting,job]);
  useEffect(()=>{
    if(!waiting)return;
    const result=answers.slice(waiting.count).find(a=>(a.parent_id||null)===waiting.parent);
    if(result){setView(v=>v===waiting.view?keyOf(result):v);setWaiting(null);setStepsOpen(false);}
  },[answers,waiting]);
  // Viewing a first question's job (e.g. after a refresh): follow it into its conversation once answered.
  // Only that job's own view moves; an empty draft or another conversation is never taken over.
  useEffect(()=>{
    if(!view.startsWith('job:')||waiting)return;
    const owner=jobs.find(j=>pendingKey(j)===view);
    const id=(owner?.result as Draft|undefined)?.id,result=id&&answers.find(a=>a.id===id);
    if(result)setView(keyOf(result));
  },[view,waiting,jobs,answers]);
  useEffect(()=>{
    // A new answer in the open conversation scrolls to its start; otherwise show the end of the thread.
    if(seen.current.view===view&&thread.length>seen.current.length)lastTurn.current?.scrollIntoView?.({block:'start'});
    else end.current?.scrollIntoView?.({block:'end'});
    seen.current={view,length:thread.length};
  },[thread.length,view,!!asked]);
  useEffect(()=>{const el=composer.current;if(!el)return;el.style.height='auto';if(el.scrollHeight)el.style.height=Math.min(el.scrollHeight,220)+'px';},[question]);

  async function send(value:string,parent:string|null=thread.at(-1)?.id||null,fromComposer=true){
    const text=value.trim();
    if(!text||blocked)return;
    setSubmitting(true);setError('');setStepsOpen(false);
    setWaiting({count:answers.length,parent,question:text,previousJob:current?.id,view});
    if(fromComposer)setQuestion('');
    try{await onSend(text,parent);}
    catch(e){setError((e as Error).message);setWaiting(null);if(fromComposer)setQuestion(q=>q||text);}
    finally{setSubmitting(false);}
  }
  function newChat(){
    // A finished job's failure or stop notice never follows into the new chat; a running job stays reachable.
    if(current&&!running)setDismissed(d=>[...d,current.id]);
    if(!running&&!starting)setWaiting(null);
    // A first question still being submitted belongs to its own (not yet created) view, not the new draft.
    else if(waiting?.view==='new')setWaiting({...waiting,view:'sent'});
    setView('new');setQuestion('');setError('');setStepsOpen(false);
    composer.current?.focus();
  }
  function choose(key:string){setView(key);setError('');setStepsOpen(false);}
  const retry=()=>outcome&&void send(outcome.request||asked||'',outcome.parent!==undefined?outcome.parent:thread.at(-1)?.id||null,false);
  const steps=job?.activity||[];
  const now=job?.state==='queued'?'Starting research':steps.at(-1)?.message||job?.stage||'Working';
  const tail=thread.at(-1);

  return <section className="cx" aria-label="Project conversation">
    <div className="cx-toolbar"><div className="cx-bar">
      {conversations.length>0?<History conversations={conversations} view={view} pendingTitle={view==='new'?'New chat':asked||'New chat'} runningKey={running?jobView:null} onSelect={choose}/>:<span className="cx-bar-title">{view==='new'?'New chat':'Current question'}</span>}
      <Button className="cx-quiet cx-new" onPress={newChat}><Glyph name="plus"/>New chat</Button>
    </div></div>
    <div className="cx-scroll" role="region" aria-label="Conversation messages" tabIndex={0}><div className="cx-column">
      {elsewhere&&<div className="cx-elsewhere" role="status"><span className="cx-pulse" aria-hidden="true"/><span>An answer is running in another conversation.</span>{jobView&&<Button className="cx-link" onPress={()=>choose(jobView)}>Show it</Button>}</div>}
      {!thread.length&&!asked&&<div className="cx-welcome">
        <h2>Ask about this project</h2>
        <p>Answers come from your project documents. Numbered citations open the original PDF page.</p>
        <ul className="cx-prompts">{PROMPTS.map(prompt=><li key={prompt}><Button className="cx-prompt" onPress={()=>{setQuestion(prompt);composer.current?.focus();}}>{prompt}</Button></li>)}</ul>
      </div>}
      {thread.map((answer,i)=><Answer key={answer.id} projectId={project.id} answer={answer} turnRef={i===thread.length-1?lastTurn:undefined} onSource={onSource} onWorkflow={onWorkflow} onSaveOffer={onSaveOffer}
        saving={jobs.some(j=>j.action==='extract_offer'&&BUSY.includes(j.state))} activity={jobs.find(j=>(j.result as Draft)?.id===answer.id)?.activity}/>)}
      {asked&&<div className="cx-turn">
        <UserMessage text={asked}/>
        {(starting||live)&&<div className="cx-live">
          <div className="cx-activity"><span className="cx-pulse" aria-hidden="true"/><span className="cx-activity-now" role="status">{starting?'Sending your question':now}</span>
            {live&&steps.length>1&&<Button className="cx-link" aria-expanded={stepsOpen} aria-controls={stepsId} onPress={()=>setStepsOpen(!stepsOpen)}>{stepsOpen?'Hide steps':`Show steps (${steps.length})`}</Button>}</div>
          {live&&steps.length>1&&<div id={stepsId} hidden={!stepsOpen}><Timeline activity={steps} live label="Live activity" onWorkflow={onWorkflow}/></div>}
        </div>}
        {outcome&&<div className={'cx-outcome'+(outcome.state==='cancelled'?' stopped':'')} role={outcome.state==='failed'?'alert':'status'}>
          <p>{outcome.state==='failed'?outcome.error||'The answer couldn’t be completed.':'Response stopped.'}{outcome.uncertain?' The request may have reached the provider. It was not retried.':''}</p>
          <Button className="cx-secondary" isDisabled={blocked} onPress={retry}>{outcome.state==='failed'?'Try again':'Ask again'}</Button>
          {!!outcome.activity?.length&&<details className="cx-outcome-steps"><summary>Steps before {outcome.state==='failed'?'the error':'stopping'}</summary><Timeline activity={outcome.activity} label="Activity before the error" onWorkflow={onWorkflow}/></details>}
        </div>}
      </div>}
      {!asked&&tail?.clarification&&<section className="cx-decision" aria-label="Your input is needed">
        <p className="cx-decision-label">Your input is needed</p>
        <h3>{tail.clarification.question}</h3>
        <div className="cx-choices">{tail.clarification.choices.map(choice=><Button key={choice} className="cx-choice" isDisabled={blocked} onPress={()=>void send(choice,tail.id,false)}>{choice}</Button>)}</div>
        <p>Choose an option or reply below in your own words.</p>
      </section>}
      <div ref={end}/>
    </div></div>
    <div className="cx-dock"><div className="cx-dock-inner">
      <form className="cx-composer" onSubmit={e=>{e.preventDefault();void send(question);}}>
        {error&&<p className="cx-composer-error" role="alert">{error}</p>}
        <TextField value={question} onChange={setQuestion} className="cx-field"><Label className="cx-sr">Ask a question</Label>
          <TextArea ref={composer} rows={1} placeholder={thread.length?'Ask a follow-up':'Ask about this project'}
            onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send(question);}}}/></TextField>
        <div className="cx-composer-bar">
          <ModelPicker disabled={running||submitting||starting} onServices={onServices} onPendingChange={setConfiguring}/>
          <Button className="cx-scope" onPress={onSources}>{ready} of {project.sources.length} documents ready</Button>
          <span className="cx-spacer"/>
          {running?<Button className="cx-stop" onPress={()=>onStop(current!.id)}><Glyph name="stop" size={14}/>Stop</Button>
            :<Button className="cx-send" type="submit" aria-label="Send question" isDisabled={blocked||!question.trim()}><Glyph name="up" size={18}/></Button>}
        </div>
      </form>
      <p className="cx-hint">Project documents only. Answers don’t change calculation inputs.{plan&&<> Uses your ChatGPT plan. <ManageUsage/></>}</p>
    </div></div>
  </section>;
}

function History({conversations,view,pendingTitle,runningKey,onSelect}:{conversations:Conversation[];view:string;pendingTitle:string;runningKey:string|null;onSelect:(key:string)=>void}) {
  const [open,setOpen]=useState(false);
  const active=conversations.find(c=>c.key===view);
  const title=active?.first.request||pendingTitle;
  function select(keys:Selection){
    if(keys==='all')return;
    const key=[...keys][0];
    if(typeof key==='string')onSelect(key);
    setOpen(false);
  }
  return <DialogTrigger isOpen={open} onOpenChange={setOpen}>
    <Button className="cx-quiet cx-history" aria-label={`Conversation history. Showing: ${title}`}><Glyph name="history"/><span className="cx-history-title">{title}</span><Glyph name="chevron" size={14}/></Button>
    <Popover className="cx-popover cx-history-pop" placement="bottom start" offset={6}>
      <Dialog className="cx-dialog">
        <Heading slot="title" className="cx-pop-heading">Conversations</Heading>
        <p className="cx-pop-sub">Kept in this project. Starting a new chat doesn’t delete them.</p>
        <ListBox aria-label="Conversations" className="cx-option-list" items={conversations} selectionMode="single" disallowEmptySelection selectedKeys={active?[active.key]:[]} onSelectionChange={select}>
          {c=><ListBoxItem id={c.key} textValue={c.first.request} className="cx-option">{({isSelected})=><>
            <span className="cx-option-name cx-history-q">{c.first.request}</span>{isSelected&&<Glyph name="check"/>}
            <small>{c.count===1?'1 answer':`${c.count} answers`}{runningKey===c.key?', answering now':''}</small></>}</ListBoxItem>}
        </ListBox>
      </Dialog>
    </Popover>
  </DialogTrigger>;
}

function UserMessage({text}:{text:string}) {
  return <div className="cx-user"><span className="cx-sr">You asked</span><p>{text}</p></div>;
}

// The model's own assessment of its evidence; worded so it never reads as independent verification.
const STATUS={supported:'Model reports support',partial:'Partial evidence',insufficient:'More information needed',conflicted:'Conflicting evidence'} as const;
// Answers saved before the grounding gate carry no grounding record; they keep this label and render unchanged.
const NOT_CHECKED='Earlier answer, not grounding-checked';
const NOT_CHECKED_DETAIL='This answer was saved before statement checks existed. Citation IDs were matched to retrieved passages and stated numbers were compared literally with the cited text. Open the citations to confirm each statement.';
// Grounding badges never say verified, true, approved or complete.
const BADGE:Record<Grounding['status'],string>={grounded:'Model-checked',withheld:'Draft withheld',abstained:'Not found',workflow:'Workflow guidance'};
const BADGE_DETAIL:Record<Grounding['status'],string>={
  grounded:'The answering model checked each published statement against quoted source text; MCDXKit then matched the quotes, citations and numbers mechanically. This is not proof that a statement is true or applies to your project.',
  withheld:'The draft didn’t pass the statement check, so it was not published.',
  abstained:'No supporting evidence was found to answer from.',
  workflow:'Guidance written by MCDXKit from the recorded project state.'};
const VERDICT:Record<GroundingStatement['verdict'],string>={supported:'Matched to quote',unsupported:'Not supported',uncertain:'Uncertain',structural:'Heading or lead-in'};
const CHECKS:Record<string,string>={
  evidence_ids:'Cited evidence is current and allowed for this statement',
  verbatim_quotes:'Each quote appears word-for-word in its passage',
  inline_citations:'Every inline citation has a quote from that passage',
  numbers:'Every number in the statement, with its sign, appears in the quotes',
  source_sentence_scope:'Each clause matches a quoted source sentence’s qualifiers, negation, number-unit conditions and limit direction, with overlapping key words. Not a full check of meaning',
  number_unit_pairs:'Each number and unit appears together in a quote',
  limit_direction:'Minimum or maximum wording matches the quote'};
const KINDS:Record<GroundingStatement['kind'],string>={heading:'Heading',section:'Statement',conflict:'Conflict'};
const auditName=(id:string)=>`mcdxkit-answer-${id}-audit.json`;
const ACTIONS:Record<WorkflowAction,string>={add_files:'Add files',index_documents:'Index documents',extract_requirements:'Extract requirements',review_requirements:'Review requirements',map_inputs:'Inspect input mapping',calculate:'Calculate current inputs',view_outputs:'View outputs'};

function Answer({projectId,answer,turnRef,onSource,onWorkflow,onSaveOffer,saving,activity}:{projectId:string;answer:Draft;turnRef?:Ref<HTMLElement>;activity?:Job['activity'];onSource:SourceHandler;onWorkflow:(action:WorkflowAction)=>void;onSaveOffer?:(answer:string)=>void;saving?:boolean}) {
  const index=useMemo(()=>citationIndex(answer.evidence),[answer.evidence]);
  const [panel,setPanel]=useState<''|'sources'|'activity'>('');
  const sourcesId=useId(),activityId=useId();
  const g=answer.grounding,state=g?.status;
  const offer=answer.extraction_offer,unsupported=answer.audit?.unsupported_numbers||[],warn=unsupported.length>0;
  const research=answer.kind!=='workflow';
  // The assessment chip is the model's status on earlier answers, or the server-adjusted status on published ones.
  const status=answer.assessment&&research&&(!g||state==='grounded')?answer.assessment.status:null;
  const coverage=answer.coverage,partial=g?.coverage==='partial'||(!!coverage&&(coverage.ready<coverage.total||coverage.excluded.length>0));
  const gaps=answer.content.gaps;
  // Withheld and abstained answers show their explanation, gaps and retrieved evidence in the main column.
  const unpublished=state==='withheld'||state==='abstained';
  const precedence=state==='grounded'?g?.scope?.precedence:undefined;
  const toggle=(name:'sources'|'activity')=>setPanel(panel===name?'':name);
  const details=<Button className="cx-link" aria-expanded={panel==='sources'} aria-controls={sourcesId} onPress={()=>setPanel('sources')}>Details</Button>;
  const notes=[warn&&'warn',partial&&'partial',precedence&&'precedence',!unpublished&&gaps.length>0&&'gaps'].filter(Boolean);
  return <article className="cx-turn" ref={turnRef}>
    <UserMessage text={answer.request}/>
    <div className="cx-answer">
      <div className="cx-byline"><span className="cx-byline-name">MCDXKit</span>
        {g&&<span className={'cx-status g-'+g.status} title={BADGE_DETAIL[g.status]}>{BADGE[g.status]}</span>}
        {g?.coverage==='partial'&&<span className="cx-status partial">Partial search</span>}
        {status&&<span className={'cx-status '+status} title={g?'Status set by MCDXKit from the checked answer and search coverage':'The model’s own assessment, not an independent check'}>{STATUS[status]}</span>}
        {!g&&research&&answer.evidence.length>0&&<span className="cx-status unchecked" title={NOT_CHECKED_DETAIL}>{NOT_CHECKED}</span>}<ModelUsed id={answer.model}/></div>
      {notes.length>0&&<ul className="cx-notes" aria-label="Before relying on this answer">
        {warn&&<li className="warn"><span><strong>Check in the source:</strong> {unsupported.flatMap(u=>u.values).slice(0,8).join(', ')} {unsupported.flatMap(u=>u.values).length===1?'was':'were'} not found word-for-word in the cited text.</span>{notes[0]==='warn'&&details}</li>}
        {partial&&<li><span>{coverage?`Searched ${coverage.ready} of ${coverage.total} documents${coverage.excluded.length?`; ${coverage.excluded.length} not searched`:''}.`:'Some documents were not searchable.'}</span>{notes[0]==='partial'&&details}</li>}
        {precedence&&<li><span className="cx-note-clamp">{precedence}</span>{notes[0]==='precedence'&&details}</li>}
        {!unpublished&&gaps.length>0&&<li><span className="cx-note-clamp">{gaps.length===1?'Limitation':`${gaps.length} limitations`}: {gaps[0]}</span>{notes[0]==='gaps'&&details}</li>}
      </ul>}
      {!answer.content.sections.length&&answer.assessment&&<Prose index={index} onSource={onSource}>{answer.assessment.explanation}</Prose>}
      {unpublished&&<div className="cx-unpublished">
        {state==='withheld'&&<p className="cx-unpublished-note">The draft wasn’t published because it didn’t pass the statement check. There is no draft to show; the original evidence is under Evidence below.</p>}
        {gaps.length>0&&<ul className="cx-gaps">{gaps.map((gap,i)=><li key={i}>{gap}</li>)}</ul>}
      </div>}
      {answer.content.sections.map((section,i)=><section key={i} className="cx-section">{section.heading&&<h3>{section.heading}</h3>}<Prose index={index} sectionIds={section.evidence_ids} onSource={onSource}>{section.body}</Prose></section>)}
      {!!answer.assessment?.conflicts.length&&<div className="cx-conflicts" role="note"><p className="cx-note-title">Sources disagree</p><ul>{answer.assessment.conflicts.map((c,i)=><li key={i}>{c}</li>)}</ul></div>}
      {offer&&!answer.extraction_ids?.length&&onSaveOffer&&<div className="cx-proposal">
        <div className="cx-proposal-head"><strong>Save these values for review?</strong><span className="cx-tag">Proposal, not saved</span></div>
        <p>Confirming extracts values and conditions from {offer.source_name}, {offer.pages.length===1?'page':'pages'} {offer.pages.join(', ')} and adjacent pages, as unreviewed requirements. Calculation inputs stay unchanged.</p>
        <Button className="cx-secondary" isDisabled={saving} onPress={()=>onSaveOffer(answer.id)}>Save for review</Button>
      </div>}
      {!!answer.extraction_ids?.length&&<div className="cx-proposal">
        <div className="cx-proposal-head"><strong>Requirements saved for review</strong></div>
        <p>Values and conditions are in Requirements. Calculation inputs are unchanged.</p>
        <Button className="cx-secondary" onPress={()=>onWorkflow('review_requirements')}>Review extracted requirements</Button>
      </div>}
      {!!answer.actions?.length&&<div className="cx-actions">{answer.actions.map(action=><Button key={action} className="cx-secondary" onPress={()=>onWorkflow(action)}>{ACTIONS[action]}</Button>)}</div>}
      {answer.evidence.length>0&&<EvidenceDisclosure answer={answer} index={index} onSource={onSource} flat={unpublished}/>}
      <div className="cx-foot">
        <Button className="cx-foot-btn" aria-expanded={panel==='sources'} aria-controls={sourcesId} onPress={()=>toggle('sources')}>Sources and checks</Button>
        {!!activity?.length&&<Button className="cx-foot-btn" aria-expanded={panel==='activity'} aria-controls={activityId} onPress={()=>toggle('activity')}>Activity <span className="cx-count">{activity.length} {activity.length===1?'step':'steps'}</span></Button>}
        <AuditDownload projectId={projectId} answerId={answer.id}/>
      </div>
      <div id={sourcesId} className="cx-panel" hidden={panel!=='sources'}><Provenance answer={answer} index={index} onSource={onSource}/></div>
      {!!activity?.length&&<div id={activityId} className="cx-panel" hidden={panel!=='activity'}><Timeline activity={activity} label="Activity steps" onWorkflow={onWorkflow}/></div>}
    </div>
  </article>;
}

const kindOf=(e:Evidence)=>e.kind==='page_context'?(e.truncated?`Original page text, first ${e.text.length} of ${e.page_characters} characters`:'Original page text'):e.kind==='extraction_quote'?'Extraction quote':'Retrieved passage';

// Complete provenance lives here, out of the reading flow: every page of every passage, checks and limits.
// The single evidence list for an answer: collapsed by default, counts in the summary, grouped passages inside.
function EvidenceDisclosure({answer,index,onSource,flat}:{answer:Draft;index:CitationIndex;onSource:SourceHandler;flat:boolean}) {
  const cited=useMemo(()=>citedIds(answer),[answer]);
  const files=new Set(answer.evidence.map(e=>e.source_id)).size;
  return <details className="cx-evidence-drop">
    <summary><span className="cx-evidence-drop-label">Evidence</span><span className="cx-count">{plural(answer.evidence.length,'record')} from {plural(files,'file')}</span></summary>
    <div className="cx-evidence-drop-body">
      <EvidenceScope answer={answer} cited={cited}/>
      <EvidenceList answer={answer} index={index} onSource={onSource} compact={flat}/>
      {!flat&&<p className="cx-legend">Filled numbers are where a citation was placed in the text; outlined numbers were cited for the whole section. A citation shows where to check a statement. It does not confirm the statement is correct.</p>}
    </div>
  </details>;
}

function Provenance({answer,index,onSource}:{answer:Draft;index:CitationIndex;onSource:SourceHandler}) {
  const audit=answer.audit,g=answer.grounding;
  return <>
    {!!audit?.unsupported_numbers.length&&<div className="cx-check-detail" role="note"><p><strong>Check these values in the source.</strong> They weren’t found word-for-word in the cited text. Open the citation before relying on them.</p>
      <ul>{audit.unsupported_numbers.map((u,i)=><li key={i}>{u.heading}: {u.values.join(', ')}</li>)}</ul></div>}
    {g&&<Ledger grounding={g} index={index} onSource={onSource}/>}
    {answer.evidence.length>0&&<p className="cx-legend">Retrieved passages and their original pages are under Evidence, below the answer.</p>}
    {!g&&answer.kind!=='workflow'&&answer.evidence.length>0&&<div className="cx-panel-block"><p className="cx-panel-title">{NOT_CHECKED}</p><p>{NOT_CHECKED_DETAIL}</p></div>}
    {answer.assessment&&<div className="cx-panel-block"><p className="cx-panel-title">{g?'Assessment written by MCDXKit':'Model assessment'}</p><p>{answer.assessment.explanation}</p></div>}
    {answer.content.gaps.length>0&&<div className="cx-panel-block"><p className="cx-panel-title">Limitations</p><ul>{answer.content.gaps.map((gap,i)=><li key={i}>{gap}</li>)}</ul></div>}
    {(answer.coverage||!!answer.searches?.length||!!answer.context_reads?.length)&&<div className="cx-panel-block"><p className="cx-panel-title">What was searched</p><ul>
      {answer.coverage&&<li>{answer.coverage.ready} of {answer.coverage.total} documents searchable{answer.coverage.excluded.length?`. Not searched: ${answer.coverage.excluded.join(', ')}`:''}</li>}
      {answer.searches?.map((s,i)=><li key={'s'+i}>Searched “{s.query}”, {s.passages} {s.passages===1?'passage':'passages'}</li>)}
      {answer.context_reads?.map((c,i)=><li key={'c'+i}>Opened page context for pages {c.pages.join(', ')} of {c.source_name}{c.truncated_pages?.length?`. Pages ${c.truncated_pages.join(', ')} truncated`:''}{c.omitted_pages?.length?`. Blank pages ${c.omitted_pages.join(', ')} skipped`:''}</li>)}
    </ul></div>}
    <p className="cx-panel-block cx-meaning">{audit?audit.meaning:'Citation identity is checked; factual completeness and applicability need review.'}</p>
    <div className="cx-panel-block"><p className="cx-panel-title">Record</p>
      <dl className="cx-record">
        <dt>Answer</dt><dd><code>{answer.id}</code></dd>
        <dt>Model</dt><dd><code>{answer.model||'Not recorded'}</code></dd>
        {typeof answer.created==='number'&&<><dt>Saved</dt><dd>{new Date(answer.created*1000).toLocaleString()}</dd></>}
        <dt>Statement check</dt><dd>{g?<code>{g.version}</code>:'None (earlier answer)'}</dd>
      </dl>
      <p className="cx-legend">The downloaded audit record is a local receipt of this answer and its evidence, with a checksum. It is not a signature or an engineering approval. Source integrity is recorded by the server in the file.</p>
    </div>
  </>;
}

// One entry per checked statement: verdict, the exact supporting quotes and their original pages.
function Ledger({grounding:g,index,onSource}:{grounding:Grounding;index:CitationIndex;onSource:SourceHandler}) {
  const statements=g.statements||[];
  const claims=statements.filter(s=>s.verdict!=='structural'),structural=statements.length-claims.length;
  const scope=[g.scope?.retrieval,g.scope?.precedence,g.scope?.approval].filter((s):s is string=>!!s);
  return <section className="cx-ledger" aria-label="Statement checks">
    <p className="cx-panel-title">Statement checks</p>
    <p className="cx-ledger-sum">{g.status==='grounded'?`${g.checked_statements??claims.length} ${(g.checked_statements??claims.length)===1?'statement':'statements'} matched to source quotes${g.inline_anchored!==undefined?`, ${g.inline_anchored} with an inline citation`:''}.`
      :g.status==='withheld'?'The draft was withheld. Its statement text is not stored; the check results are listed by statement number.'
      :g.status==='abstained'?'No statements were published.':'Guidance written from the recorded project state; no document statements to check.'}</p>
    {claims.length>0&&<ol className="cx-claims">{claims.map(s=><Claim key={s.id} statement={s} index={index} onSource={onSource}/>)}</ol>}
    {structural>0&&<p className="cx-legend">{structural} {structural===1?'heading or lead-in was':'headings or lead-ins were'} treated as structure, not checked as claims.</p>}
    {(g.method||scope.length>0||g.meaning)&&<details className="cx-method"><summary>How answers are checked</summary>
      {g.method&&<p>{g.method}</p>}{g.meaning&&<p>{g.meaning}</p>}
      {scope.length>0&&<ul>{scope.map((s,i)=><li key={i}>{s}</li>)}</ul>}
    </details>}
  </section>;
}

function Claim({statement:s,index,onSource}:{statement:GroundingStatement;index:CitationIndex;onSource:SourceHandler}) {
  const checks=s.checks_applied;
  return <li className={'cx-claim v-'+s.verdict}>
    <div className="cx-claim-head"><span className={'cx-verdict v-'+s.verdict}>{VERDICT[s.verdict]||s.verdict}</span><small>{s.id}</small><small>{KINDS[s.kind]||s.kind}{s.section!==null&&s.kind!=='conflict'?`, section ${s.section+1}`:''}</small>{s.anchored&&<small>Inline citation</small>}</div>
    {s.text?<p className="cx-claim-text">{s.text}</p>:<p className="cx-claim-missing">Statement text not published.</p>}
    {(s.quotes||[]).map((q,i)=>{const cited=index.get(q.evidence_id),e=cited?.evidence;return <figure key={i} className="cx-quote">
      <blockquote>{q.text}</blockquote>
      <figcaption>{cited&&e?<><span className="cx-pill static" aria-hidden="true">{cited.n}</span><span className="cx-quote-source">{e.source_name}</span>
        {pagesOf(e).map(page=><Button key={page} className="cx-page" aria-label={`Open ${e.source_name}, page ${page}, at the quote for ${s.id}`} onPress={()=>onSource(e.source_id,page,q.text)}>Page {page}</Button>)}
        {!pagesOf(e).length&&<Button className="cx-page" aria-label={`Open ${e.source_name} at the quote for ${s.id}, page not recorded`} onPress={()=>onSource(e.source_id,1,q.text)}>Open PDF, page not recorded</Button>}</>
        :<span>Evidence {q.evidence_id} is not in this answer’s record.</span>}</figcaption>
    </figure>;})}
    {(s.reasons.length>0||checks!==undefined)&&<details className="cx-claim-why"><summary>Check details</summary>
      {checks!==undefined&&(checks.length?<ul>{checks.map(c=><li key={c}>{CHECKS[c]||`${c.replace(/_/g,' ').replace(/^./,x=>x.toUpperCase())} (check named by the server; no description available here)`}</li>)}</ul>:<p>Nothing beyond the model’s label could be checked mechanically for this statement.</p>)}
      {s.reasons.length>0&&<ul className="cx-reasons">{s.reasons.map((r,i)=><li key={i}>{r}</li>)}</ul>}
    </details>}
  </li>;
}

// Passages whose text is identical after collapsing whitespace are shown once; nothing fuzzier is merged.
// Records, IDs and citation numbers are unchanged; every copy keeps its own source and page controls.
type EvidenceGroup={key:string;text:string;members:Evidence[];cited:boolean};
const normalText=(text:string)=>text.replace(/\s+/g,' ').trim();
function groupEvidence(evidence:Evidence[],cited:Set<string>):EvidenceGroup[] {
  const groups=new Map<string,EvidenceGroup>();
  for(const e of evidence){
    const key=normalText(e.text)||'id:'+e.id,group=groups.get(key);
    if(group){group.members.push(e);group.cited||=cited.has(e.id);}
    else groups.set(key,{key,text:e.text,members:[e],cited:cited.has(e.id)});
  }
  return [...groups.values()];
}
// IDs the answer relies on: section evidence, inline pills (collected by the same parse and traversal that renders
// them), audit-cited IDs and statement-check evidence.
function citedIds(answer:Draft) {
  const known=citationIndex(answer.evidence);
  const bodies=answer.content.sections.length?answer.content.sections.map(s=>s.body):answer.assessment?[answer.assessment.explanation]:[];
  const ids=new Set<string>([...(answer.audit?.cited||[]),...answer.content.sections.flatMap(s=>s.evidence_ids),...bodies.flatMap(b=>inlineCitationIds(b,known))]);
  for(const s of answer.grounding?.statements||[]){s.evidence_ids?.forEach(id=>ids.add(id));s.quotes?.forEach(q=>ids.add(q.evidence_id));}
  return ids;
}
const plural=(n:number,one:string,many=one+'s')=>`${n} ${n===1?one:many}`;

// Counts kept separate so a pile of retrieval records never reads as broad support.
function EvidenceScope({answer,cited}:{answer:Draft;cited:Set<string>}) {
  const used=answer.evidence.filter(e=>cited.has(e.id));
  const pages=new Set(used.flatMap(e=>pagesOf(e).map(p=>e.source_id+'\u0000'+p)));
  const texts=new Set(used.map(e=>normalText(e.text)||'id:'+e.id));
  const files=new Set(answer.evidence.map(e=>e.source_id));
  // Original page context: prefer page_context records (file ID + page); older answers only have context_reads,
  // which identify the file by name. Omitted (blank) pages are not counted; truncated pages are shown as partial.
  const context=answer.evidence.filter(e=>e.kind==='page_context');
  const byRecord=context.length>0;
  const pageKeys=(e:Evidence)=>pagesOf(e).map(p=>e.source_id+'\u0000'+p);
  const reads=byRecord?new Set(context.flatMap(pageKeys))
    :new Set((answer.context_reads||[]).flatMap(c=>c.pages.filter(p=>!c.omitted_pages?.includes(p)).map(p=>c.source_name+'\u0000'+p)));
  const truncated=byRecord?new Set(context.filter(e=>e.truncated).flatMap(pageKeys))
    :new Set((answer.context_reads||[]).flatMap(c=>(c.truncated_pages||[]).filter(p=>!c.omitted_pages?.includes(p)).map(p=>c.source_name+'\u0000'+p)));
  const searches=answer.searches?.length||0;
  return <>
    <ul className="cx-scope" aria-label="Evidence scope">
      {used.length>0&&<li><strong>Cited</strong> {plural(used.length,'passage')}{texts.size<used.length?` (${plural(texts.size,'distinct text')})`:''} on {plural(pages.size,'original page')}</li>}
      {reads.size>0&&<li><strong>Page context</strong> {plural(reads.size,'original page')} inspected{truncated.size?`, ${truncated.size} only in part (text cut off)`:''}{byRecord?'':', counted by file name'}. Only pages around selected passages are opened, never whole documents.</li>}
      <li><strong>Retrieved</strong> {plural(answer.evidence.length,'record')} from {plural(files.size,'file')}{searches?` in ${plural(searches,'search','searches')}`:''}. Search returns top-ranked passages, not every page.</li>
    </ul>
    {texts.size<used.length&&<p className="cx-legend">Some cited passages are the same text in more than one record or file copy. They are shown once below and are not independent confirmation.</p>}
    {used.length>1&&<p className="cx-legend">Several statements can cite the same clause when it states more than one requirement.</p>}
  </>;
}

function EvidenceList({answer,index,onSource,compact=false}:{answer:Draft;index:CitationIndex;onSource:SourceHandler;compact?:boolean}) {
  const cited=useMemo(()=>citedIds(answer),[answer]);
  const groups=useMemo(()=>groupEvidence(answer.evidence,cited),[answer.evidence,cited]);
  // Withheld answers cite nothing, so the compact list stays flat; otherwise uncited records sit behind one disclosure.
  const main=compact?groups:groups.filter(g=>g.cited),rest=compact?[]:groups.filter(g=>!g.cited);
  const list=(items:EvidenceGroup[])=><ol className={'cx-evidence'+(compact?' compact':'')}>{items.map(g=><EvidenceItem key={g.key} group={g} index={index} cited={cited} onSource={onSource} showCited={!compact}/>)}</ol>;
  return <>
    {main.length>0&&list(main)}
    {rest.length>0&&<details className="cx-more-evidence" open={!main.length||undefined}><summary>{main.length?'Other retrieved passages':'Retrieved passages'} <span className="cx-count">{rest.length}</span></summary>{list(rest)}</details>}
  </>;
}

function EvidenceItem({group:g,index,cited,onSource,showCited}:{group:EvidenceGroup;index:CitationIndex;cited:Set<string>;onSource:SourceHandler;showCited:boolean}) {
  const first=g.members[0],repeated=g.members.length>1;
  const files=new Set(g.members.map(e=>e.source_id)).size;
  const preview=normalText(g.text);
  return <li>
    <span className="cx-pills" aria-hidden="true">{g.members.map(e=><span key={e.id} className="cx-pill static">{index.get(e.id)?.n}</span>)}</span>
    <div>
      <p className="cx-ev-head"><strong>{first.source_name}</strong>
        {repeated&&<span className="cx-tag" title="Identical text after collapsing whitespace. Not independent confirmation.">{files>1?`Same passage in ${files} files`:`Same text in ${g.members.length} records`}</span>}
        {!repeated&&<small>{kindOf(first)}</small>}
        {showCited&&<small>{g.cited?'Cited':'Not cited in the answer'}</small>}</p>
      <ul className="cx-copies">{g.members.map(e=>{const pages=pagesOf(e),label=repeated?`${e.source_name} · p. %p (${e.id})`:`${e.source_name} · p. %p`;return <li key={e.id}>
        {repeated&&<span className="cx-copy-name"><span className="cx-pill static" aria-hidden="true">{index.get(e.id)?.n}</span>{e.source_name}</span>}
        <small>{e.id}{repeated?`, ${kindOf(e).toLowerCase()}`:''}{showCited&&repeated&&!cited.has(e.id)?', not cited':''}</small>
        {pages.length?pages.map(page=><Button key={page} className="cx-page" aria-label={label.replace('%p',String(page))} onPress={()=>onSource(e.source_id,page,quoteOf(e))}>Page {page}</Button>)
          :<Button className="cx-page" aria-label={`${e.source_name} · original PDF (page not recorded)${repeated?` (${e.id})`:''}`} onPress={()=>onSource(e.source_id,1,quoteOf(e))}>Open PDF, page not recorded</Button>}
      </li>;})}</ul>
      <details className="cx-excerpt"><summary><span className="cx-sr">Show passage text</span><span className="cx-excerpt-preview" aria-hidden="true">{preview}</span></summary><blockquote>{g.text}</blockquote></details>
    </div></li>;
}

// Authenticated download of the server's audit record; the UI never builds the record itself.
function AuditDownload({projectId,answerId}:{projectId:string;answerId:string}) {
  const [state,setState]=useState<{kind:'idle'|'busy'|'done'|'error';message?:string}>({kind:'idle'});
  const alive=useRef(true);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  async function fetchRecord(){
    setState({kind:'busy'});
    try{
      await download(`/api/documents/${encodeURIComponent(projectId)}/answers/${encodeURIComponent(answerId)}/audit`,auditName(answerId));
      if(alive.current)setState({kind:'done'});
    }catch(e){if(alive.current)setState({kind:'error',message:(e as Error).message});}
  }
  return <>
    <Button className="cx-foot-btn" isDisabled={state.kind==='busy'} onPress={()=>void fetchRecord()}>{state.kind==='busy'?'Preparing audit record…':'Download audit record'}</Button>
    {state.kind==='done'&&<span className="cx-foot-msg" role="status">Downloaded {auditName(answerId)}</span>}
    {state.kind==='error'&&<span className="cx-foot-msg error" role="alert">Audit record not downloaded. {state.message}</span>}
  </>;
}

const ACTIVITY_LABELS:Record<ActivityType,string>={step:'',search:'Search',context:'Original pages',extraction:'Saved for review',check:'Citation check'};
// Observed operations only: steps, searches, page reads, saved extractions and checks. No model reasoning text.
function Timeline({activity,live=false,label,onWorkflow}:{activity:Activity[];live?:boolean;label:string;onWorkflow:(action:WorkflowAction)=>void}) {
  return <ol className="cx-steps" aria-label={label}>{activity.map((step,i)=>{
    const type=step.type||'step',d=step.detail||{},now=live&&i===activity.length-1;
    return <li key={i} className={'cx-step-'+type} aria-current={now?'step':undefined}>
      {ACTIVITY_LABELS[type]&&<small>{ACTIVITY_LABELS[type]}</small>}<span>{step.message}</span>
      {type==='search'&&!!d.sources?.length&&<em>{d.sources.join(', ')}</em>}
      {type==='context'&&!!d.pages?.length&&<em>{d.pages.length===1?'Page ':'Pages '}{d.pages.join(', ')}</em>}
      {type==='extraction'&&live&&d.facts!==undefined&&<Button className="cx-link" onPress={()=>onWorkflow('review_requirements')}>Review</Button>}
      {type==='check'&&d.status==='warnings'&&<em>Some stated numbers were not found in their cited text.</em>}
    </li>;})}</ol>;
}
