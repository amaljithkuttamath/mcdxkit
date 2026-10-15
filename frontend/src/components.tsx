import {Button, Checkbox} from 'react-aria-components';
import type {CalculateAllResult, Fact, Job, Source} from './types';

const STATUS_LABEL={succeeded:'Calculated',skipped:'Already calculated',failed:'Failed'};

export function JobStatus({job,onCancel}:{job:Job;onCancel:()=>void}) {
  const active=['queued','running'].includes(job.state);
  // Measured progress only: a determinate bar needs counts the backend recorded (finished of total).
  const measured=[...(job.activity||[])].reverse().find(a=>typeof a.detail?.total==='number'&&typeof a.detail?.finished==='number')?.detail;
  return <div className={'job-status '+job.state} role={job.state==='failed'?'alert':'status'}>
    <div><strong>{job.stage}</strong>{job.error&&<p>{job.error}</p>}
    {job.uncertain&&<p>The remote request may have completed. Check saved results before starting another request.</p>}</div>
    {active&&<>{measured?<progress aria-label={job.stage} value={measured.finished} max={measured.total}/>:<progress aria-label={job.stage}/>}<Button onPress={onCancel}>Cancel</Button></>}
    {job.state==='succeeded'&&<span>Saved</span>}
  </div>;
}

// Outcome of the latest Calculate all, one row per calculation. Finishing the job is not the same as every
// calculation succeeding, so failures stay named here until the engineer dismisses the summary.
export function CalculateAllSummary({job,current,onSelect,onDismiss}:{job:Job;current?:string;onSelect:(id:string)=>void;onDismiss:()=>void}) {
  const result=job.result as CalculateAllResult;
  return <section className={'batch-summary'+(result.failed?' has-failures':'')} aria-label="Calculate all results">
    <div className="batch-summary-head"><div><strong>Calculate all · {result.succeeded} calculated, {result.skipped} already calculated, {result.failed} failed</strong>
      {result.skipped>0&&<p>Already calculated: a saved run with the same inputs and engine was reused, so no duplicate run was made.</p>}</div>
      <Button onPress={onDismiss}>Dismiss</Button></div>
    <ul>{result.calculations.map(c=><li key={c.calculation_id} className={c.status}>
      <Button aria-current={c.calculation_id===current?'true':undefined} onPress={()=>onSelect(c.calculation_id)}>{c.name}</Button>
      <span>{STATUS_LABEL[c.status]}</span>{c.error&&<small role="alert">{c.error}</small>}</li>)}</ul>
  </section>;
}

// The literal source text stays verbatim; the unit recorded with the extraction is shown beside it,
// because literals such as "two (2)" carry no unit of their own.
export function FactValue({fact}:{fact:Pick<Fact,'value_text'|'unit'>}) {
  return <span className="fact-value">{fact.value_text} <span className="fact-unit" title="Unit recorded with the extracted value">{fact.unit}</span></span>;
}

export function RequirementRow({fact,source,selected,onSelect,onSource,onReview}:{fact:Fact;source:Source;selected:boolean;onSelect:()=>void;onSource:(id:string,page:number,quote:string)=>void;onReview:()=>void}) {
  return <article className="requirement-row">
    <div className="requirement-heading"><Checkbox isSelected={selected} onChange={onSelect} aria-label={'Select '+fact.property+' for '+fact.subject}><span className="check-mark"/>{fact.property}</Checkbox><strong className="value"><FactValue fact={fact}/></strong></div>
    <p className="subject">{fact.subject}</p>
    {fact.conditions.length>0?<ul className="conditions">{fact.conditions.map((c,i)=><li key={i}>{c.text} <Button className="citation" onPress={()=>onSource(source.id,c.page,c.quote)}>Condition · p. {c.page}</Button></li>)}</ul>:<p className="muted">No conditions extracted. Check the surrounding section.</p>}
    <div className="row-actions">{fact.evidence.map((e,i)=><Button key={i} className="citation" onPress={()=>onSource(source.id,e.page,e.quote)}>{source.name} · p. {e.page}</Button>)}
      <span className={'review-state '+(fact.review?.status||'pending')}>{fact.review?.status==='reviewed'?'Review recorded':fact.review?.status==='rejected'?'Rejected':'Needs review'}</span>
      <Button onPress={onReview}>Review</Button></div>
  </article>;
}

export function Icon({name}:{name:'sources'|'sheet'|'close'|'plus'|'settings'}) {
  const paths={sources:'M8 4h11v15H8z M5 7H3v14h13v-2',sheet:'M6 3h9l4 4v14H6z M14 3v5h5 M9 12h7 M9 16h7',close:'m6 6 12 12 M18 6 6 18',plus:'M12 5v14 M5 12h14',settings:'M4 7h16 M4 17h16 M9 4v6 M15 14v6'};
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
