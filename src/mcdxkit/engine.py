"""Public conversion engine independent of CLI, agent host, HTTP and Mathcad."""
import csv
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from . import group_report, mcdx, calcpad
from . import checks as worksheet_checks
from .review import runner as review_runner



def _read_raw(path):
    path=Path(path)
    if path.stat().st_size>16*1024*1024:raise ValueError('Raw report exceeds 16 MiB')
    return path.read_bytes()


def _parse_raw(raw, load_source):
    if b'\x00' in raw:raise ValueError('Expected a text GROUP report, not a binary workbook')
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:text=raw.decode('cp1252')
    return group_report.parse(text,load_source)


def _read_report(path, load_source):
    raw=_read_raw(path)
    return raw,_parse_raw(raw,load_source)


def _governing(block):
    rows={a['component']:a['data'] for a in block['annotations'] if a['kind']=='governing'}
    return rows or None


def _case_suggestions(parsed, block):
    """Summarise classifier suggestions; recommended strength cases are checked with the same
    select rules as a manual selection, and never applied here."""
    rows=[s for s in block['suggestions'] if s['kind']=='limit_state']
    if not rows:return None
    names={c['id']:c['name'] for c in parsed['cases']}
    recommended=[s['case'] for s in rows if s['value']=='strength']
    issue=None
    try:group_report.select(parsed,recommended)
    except ValueError as exc:issue=str(exc)
    basis=rows[0]['basis']
    return {'classifier':rows[0]['classifier'],'trained_on':{'seed':basis.get('seed'),'history':basis.get('history'),
                                                     'history_skipped':basis.get('history_skipped')},
            'cases':[{'id':s['case'],'name':names.get(s['case']),'category':s['value'] or 'unknown',
                      'confidence':s['confidence'],'evidence':s['evidence'],'stage':s['basis'].get('stage')} for s in rows],
            'recommended_cases':recommended,'selection_issue':issue,
            'unresolved_case_ids':[s['case'] for s in rows if s['value'] is None]}


DECISIONS=('included_case','kept_service','will_fix_in_group')
DECISION_FIELDS={'rule','case','component','decision','note','decided_at'}
# ISO 8601 date and time (minutes required; seconds, milliseconds or microseconds optional),
# with an optional Z or +hh:mm offset. Explicit so Python 3.10 and 3.12 accept the same values.
DECIDED_AT=re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{3}(?:\d{3})?)?)?(?:Z|[+-]\d{2}:\d{2})?')


def _decided_at(value):
    if not isinstance(value,str) or not DECIDED_AT.fullmatch(value):return False
    try:datetime.fromisoformat(value[:-1]+'+00:00' if value.endswith('Z') else value)
    except ValueError:return False
    return True


def _decisions(block, decisions):
    """Validate engineer decisions on raised flags. Each names a flag (rule, case, component),
    one of DECISIONS, an optional note and an ISO 8601 decided_at date and time.

    A decision whose rule ran successfully must match a flag it raised. When the rule did not
    run successfully (error, timeout, skipped or not enabled) the decision cannot be checked;
    it is kept in the audit with ``unverified: true`` instead of failing the conversion."""
    if decisions is None:return None
    if not isinstance(decisions,(list,tuple)) or len(decisions)>500:
        raise ValueError('review_decisions must be a list of at most 500 decisions')
    flags={(f['rule'],f['case'],f['component']) for f in block['flags']}
    ran={c['id'] for c in block['checks_run'] if c['kind']=='check' and c['status']=='ok'}
    result=[];seen=set()
    for item in decisions:
        if not isinstance(item,dict) or set(item)-DECISION_FIELDS or not {'rule','case','component','decision','decided_at'}<=set(item):
            raise ValueError('Each review decision is {rule, case, component, decision, note, decided_at}')
        if (not isinstance(item['rule'],str) or not isinstance(item['decision'],str)
                or not (item['case'] is None or (isinstance(item['case'],int) and not isinstance(item['case'],bool)))
                or not (item['component'] is None or isinstance(item['component'],str))):
            raise ValueError('Review decision rule and decision are text, case is an integer or null, component is text or null')
        key=(item['rule'],item['case'],item['component'])
        if key in seen:raise ValueError(f'More than one review decision for flag {key}')
        seen.add(key)
        if item['decision'] not in DECISIONS:
            raise ValueError('Review decision must be one of '+', '.join(DECISIONS))
        note=item.get('note')
        if note is not None and (not isinstance(note,str) or len(note)>500):
            raise ValueError('Review decision note must be text of at most 500 characters')
        if not _decided_at(item['decided_at']):
            raise ValueError('Review decision decided_at must be an ISO 8601 date and time such as 2026-10-06T09:30:00Z')
        unverified=key not in flags
        if unverified and key[0] in ran:
            raise ValueError(f'Review decision does not match a raised flag: {key}')
        row={'rule':key[0],'case':key[1],'component':key[2],'decision':item['decision'],
             'note':note,'decided_at':item['decided_at']}
        if unverified:row['unverified']=True
        result.append(row)
    return result


def review_note(block):
    """Plain-text worksheet note, written only when at least one review decision exists."""
    decisions=block.get('decisions')
    if not decisions:return None
    run=sum(c['kind']=='check' and c['status']=='ok' for c in block['checks_run'])
    raised=len(block['flags'])
    items=f'{raised} item{"" if raised==1 else "s"} raised'
    verified=sum(not d.get('unverified') for d in decisions)
    reviewed=('reviewed by the engineer' if verified>=raised else f'{verified} reviewed by the engineer') if verified else 'no verified review decisions'
    unverified=len(decisions)-verified
    if unverified:reviewed+=f', {unverified} unverified decision(s)'
    return (f'Automated input checks: {run} run, {items}, {reviewed}\n'
            'Checks review the GROUP summary for consistency. They do not verify the design.')


def inspect_template(template, *, input_map=None):
    """Discover actual scalar input definitions, including templates with custom variable names."""
    from .inspection import template_inputs
    return template_inputs(template,input_map=input_map)


def inspect_report(report, *, cases=None, load_source='effects', checks='default', history=None, template=None, input_map=None, overrides=None):
    """Inspect available cases; selection_required explains unresolved classification.

    ``checks`` selects review plug-ins ('default', 'none', a comma list of ids, or a plan from
    review.runner.prepare() resolved once by a long-running caller); ``history``
    is an optional review.api.HistoryStore the case-name classifier learns from."""
    plan=review_runner.prepare(checks)
    _,parsed=_read_report(report,load_source)
    issue=None
    try:selected=group_report.select(parsed,cases)
    except ValueError as exc:
        if cases is not None:raise
        selected=[];issue=str(exc)
    block=review_runner.review(parsed,[c['id'] for c in selected],plan=plan,history=history)
    result={'source':Path(report).name,'summary_only':parsed['summary_only'],
            'cases':[{'id':c['id'],'name':c['name']} for c in parsed['cases']],
            'selected_cases':[c['id'] for c in selected],'selection_required':issue,
            'load_source':load_source,'envelope':group_report.envelope(selected) if selected else None,
            'governing':_governing(block) if selected else None,
            'case_suggestions':_case_suggestions(parsed,block),
            'review_checks':block,
            'largest_observed_pile_id':max((p for c in parsed['cases'] for p in c['pile_ids']),default=0)}
    if template is not None:
        from .inspection import template_report
        result.update(template_report(template,result,input_map,overrides))
    elif input_map is not None:
        raise ValueError('Inspecting an input map requires --template')
    return result


def convert(report, template, output, *, cases=None, load_source='effects', title=None, overrides=None,
            checks='default', review_decisions=None, input_map=None):
    """Generate .mcdx, executable .cpd, calculated .html and .audit.json. Never overwrite sources or existing outputs.

    The advisory review (``checks``) and any ``review_decisions`` are recorded in the audit. Only when
    a decision exists does the worksheet gain a plain-text "Automated input checks" note; otherwise
    the .mcdx, .cpd and .html are identical with checks on or off."""
    plan=review_runner.prepare(checks)
    report=Path(report).resolve();template=Path(template).resolve();output=Path(output).resolve()
    audit=output.with_suffix('.audit.json')
    if output.suffix.lower()!='.mcdx':raise ValueError('Output filename must end in .mcdx')
    if output in (report,template) or audit in (report,template):raise ValueError('Output cannot replace a source')
    cpd=output.with_suffix('.cpd'); calculated=output.with_suffix('.html')
    if any(p.exists() for p in (output,audit,cpd,calculated)):raise ValueError('Output artifact already exists; choose a new filename')
    if not template.is_file():raise ValueError('Template not found. Provide --template /path/to/reference.mcdx')
    raw,parsed=_read_report(report,load_source)
    selected=group_report.select(parsed,cases)
    block=review_runner.review(parsed,[c['id'] for c in selected],plan=plan)
    decisions=_decisions(block,review_decisions)
    if decisions is not None:block['decisions']=decisions
    output.parent.mkdir(parents=True,exist_ok=True)
    # Stage both artifacts before publishing either. Hard links fail if a destination
    # was concurrently created and avoid replacing it; all paths share a filesystem.
    with tempfile.TemporaryDirectory(prefix='.mcdxkit-',dir=output.parent) as folder:
        staged=Path(folder)/'worksheet.mcdx'
        result=mcdx.generate(template,staged,selected,report.name,title,overrides,geometry_cases=parsed['cases'],
                             review_note=review_note(block),input_map=input_map)
        result.update({'output':str(output),'audit':str(audit),'source_sha256':hashlib.sha256(raw).hexdigest(),
                       'geometry_case_ids':[c['id'] for c in parsed['cases']],
                       'case_names':{str(c['id']):c['name'] for c in parsed['cases']},
                       'review_checks':block})
        cpd_staged=Path(folder)/'worksheet.cpd'; html_staged=Path(folder)/'worksheet.html'
        calculation=calcpad.calculate(staged,cpd_staged,html_staged)
        result.update({'calculation':calculation,'open_worksheet':str(cpd),'calculated_worksheet':str(calculated)})
        # Outcomes are read from the published snapshot, never recalculated.
        found,summary=worksheet_checks.extract(html_staged.read_text(encoding='utf-8'),calculation['region_lines'])
        result.update({'checks':found,'check_summary':summary})
        audit_staged=Path(folder)/'audit.json'
        audit_staged.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        published=[]
        try:
            # Audit is the completion marker, published last.
            for source,destination in ((staged,output),(cpd_staged,cpd),(html_staged,calculated),(audit_staged,audit)):
                os.link(source,destination);published.append((source,destination))
        except OSError:
            for source,destination in published:
                if destination.exists() and os.path.samefile(destination,source):destination.unlink()
            raise
    return result


SUMMARY_MEASURES=(('P','kip'),('Vy','kip'),('Vz','kip'),('My','kip_in'),('Mz','kip_in'))
SUMMARY_COLUMNS=(['report','origin','source_sha256','load_source','case_id','case_name','selected']+
                 [key+'_'+unit for key,unit in SUMMARY_MEASURES]+['governs'])


def _summary_rows(name, origin, digest, parsed, selected):
    """One row per final-summary case. Values are that case's component maxima (P: max compression,
    others: max magnitude); `governs` names measures for which the case is the selected-case maximum."""
    governing=group_report.envelope(selected)
    chosen={c['id'] for c in selected}
    rows=[]
    for case in parsed['cases']:
        values=group_report.envelope([case])
        row={'report':name,'origin':origin,'source_sha256':digest,
             'load_source':parsed['load_source'],'case_id':case['id'],'case_name':case['name'],
             'selected':case['id'] in chosen}
        row.update({key+'_'+unit:values[key] for key,unit in SUMMARY_MEASURES})
        row['governs']=[key for key,_ in SUMMARY_MEASURES if case['id'] in chosen and values[key]==governing[key]]
        rows.append(row)
    return rows


def _select(label, parsed, cases):
    try:return group_report.select(parsed,cases)
    except ValueError as exc:raise ValueError(f'{label}: {exc}') from exc


def summary_key(rows):
    """Identity of one summarized input: identical source bytes, load basis and selection give identical rows."""
    return (rows[0]['source_sha256'],rows[0]['load_source'],tuple(r['case_id'] for r in rows if r['selected']))


def summarize_report(report, *, cases=None, load_source='effects', origin=None):
    """Summary rows for one report. Unresolved or invalid case selection raises ValueError."""
    report=Path(report)
    raw,parsed=_read_report(report,load_source)
    return _summary_rows(report.name,origin or report.name,hashlib.sha256(raw).hexdigest(),parsed,
                         _select(report.name,parsed,cases))


def load_audit(audit):
    """Bounded JSON read of a conversion audit; rejects symlinked audits and audit folders."""
    audit=Path(audit)
    if audit.is_symlink() or audit.parent.is_symlink() or not audit.is_file() or audit.stat().st_size>2*1024*1024:
        raise ValueError(f'{audit.name}: unsupported audit file')
    try:data=json.loads(audit.read_text(encoding='utf-8'))
    except (ValueError,UnicodeError) as exc:raise ValueError(f'{audit.name}: unreadable audit JSON') from exc
    if not isinstance(data,dict):raise ValueError(f'{audit.name}: unreadable audit JSON')
    return data


def summarize_audit(audit, *, origin=None):
    """Summary rows for a completed conversion, parsed from its `_source` snapshot only after the
    snapshot matches the audited SHA-256, with the audit's recorded cases and load basis."""
    audit=Path(audit)
    data=load_audit(audit)
    name,expected,cases,basis=(data.get(k) for k in ('source','source_sha256','cases','load_source'))
    if (not isinstance(name,str) or Path(name).name!=name or name in ('','.','..') or not isinstance(expected,str)
            or not isinstance(cases,list) or not all(type(i) is int for i in cases) or basis not in ('effects','reactions')):
        raise ValueError(f'{audit.name}: not an MCDXKit conversion audit')
    folder=audit.parent/'_source';snapshot=folder/name
    if folder.is_symlink() or snapshot.is_symlink() or not snapshot.is_file():
        raise ValueError(f'{audit.name}: source snapshot _source/{name} is missing or not a regular file')
    raw=_read_raw(snapshot);digest=hashlib.sha256(raw).hexdigest()
    if digest!=expected:
        raise ValueError(f'{audit.name}: source snapshot does not match the audited SHA-256')
    parsed=_parse_raw(raw,basis)
    return _summary_rows(name,origin or audit.name,digest,parsed,_select(audit.name,parsed,cases))


def summary_csv(rows):
    """Render summary rows as CSV text (units in column names, kip and kip-in)."""
    def text(value):
        # Spreadsheet formula injection: report/case names are untrusted text.
        return "'"+value if isinstance(value,str) and value[:1] in ('=','+','-','@','\t','\r') else value
    def cell(key,value):
        if isinstance(value,bool):return 'yes' if value else 'no'
        if isinstance(value,float):return format(value,'.12g')
        if isinstance(value,list):return ' '.join(value)
        return '' if value is None else text(value)
    stream=io.StringIO()
    writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(SUMMARY_COLUMNS)
    for row in rows:writer.writerow([cell(key,row[key]) for key in SUMMARY_COLUMNS])
    return stream.getvalue()


def validate(worksheet):
    """Check ZIP/XML relationships. Does not execute Mathcad or validate PTC schemas."""
    return mcdx.validate(worksheet)
