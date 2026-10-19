"""Read-only AG-UI 1.0 projection of durable jobs, built on the official ag-ui-protocol SDK.

The SQLite job ledger stays authoritative for claims, cancellation, replay and
human decisions. This adapter only re-encodes stored job snapshots: it starts no
runs, accepts no RunAgentInput, executes no frontend tools and emits no model
text or reasoning. Activity types under ``mcdxkit.*`` are this producer's own
open ``activityType`` values, as the AG-UI specification allows.
"""
from ag_ui.core import (ActivitySnapshotEvent, RunErrorEvent, RunFinishedCancelledOutcome,
                        RunFinishedEvent, RunStartedEvent)
from ag_ui.encoder import EventEncoder

PROTOCOL = '1.0'
START, END = 'run', 'end'


def _ms(seconds):
    return int(seconds * 1000)


def _conversation(job, project):
    """The saved Ask conversation this run belongs to, when it is already known."""
    answers = {a['id']: a for a in project.get('answers', [])}
    result = job.get('result')
    if job['action'] == 'ask' and isinstance(result, dict) and result.get('id') in answers:
        return result.get('conversation_id') or result['id']
    parent = answers.get(job.get('parent') or '')
    return (parent.get('conversation_id') or parent['id']) if parent else None


def events(job, project, last_event_id=None):
    """Return (event_id, event) pairs for a public job snapshot, after a reconnect position.

    Event IDs: ``run`` for RUN_STARTED, the durable activity ``seq`` for activity
    snapshots, ``end`` for the terminal event. Replay covers the bounded activity
    retained by the job ledger (most recent 40 entries)."""
    if last_event_id == END: return []
    after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    # AG-UI threads are conversations. A new Ask conversation is identified by its first
    # saved answer, which does not exist when the run starts, so runs are threaded by
    # project and the saved conversation is carried in metadata once known.
    metadata = {'mcdxkit': {'project': job['project'], 'action': job['action'],
                            'conversation_id': _conversation(job, project), 'parent_answer_id': job.get('parent')}}
    thread = job['project']
    found = []
    if last_event_id is None:
        found.append((START, RunStartedEvent(thread_id=thread, run_id=job['id'], protocol_version=PROTOCOL,
                                             timestamp=_ms(job['created']), metadata=metadata)))
    for item in job.get('activity') or []:
        if item['seq'] <= after: continue
        found.append((str(item['seq']), ActivitySnapshotEvent(
            message_id=f"{job['id']}-{item['seq']}", activity_type='mcdxkit.' + item.get('type', 'step'),
            content={'message': item['message'], **(item.get('detail') or {})}, timestamp=_ms(item['time']))))
    state = job['state']
    if state == 'succeeded':
        result = job.get('result') if isinstance(job.get('result'), dict) else {}
        # References only; clients read the saved record through the authenticated API.
        summary = {'record_id': result.get('id'), 'conversation_id': metadata['mcdxkit']['conversation_id'],
                   'clarification_requested': bool(result.get('clarification')),
                   'extraction_ids': result.get('extraction_ids', [])}
        found.append((END, RunFinishedEvent(thread_id=thread, run_id=job['id'], result=summary,
                                            timestamp=_ms(job['updated']), metadata=metadata)))
    elif state == 'cancelled':
        # Nothing is published from a cancelled run; a remote request may still have been charged.
        metadata['mcdxkit']['uncertain_remote_request'] = job['uncertain']
        found.append((END, RunFinishedEvent(thread_id=thread, run_id=job['id'], outcome=RunFinishedCancelledOutcome(),
                                            timestamp=_ms(job['updated']), metadata=metadata)))
    elif state == 'failed':
        found.append((END, RunErrorEvent(message=job.get('error') or 'Operation failed.',
                                         code='uncertain_remote_request' if job['uncertain'] else 'failed',
                                         timestamp=_ms(job['updated']), metadata=metadata)))
    return found


ENCODER = EventEncoder()
CONTENT_TYPE = ENCODER.get_content_type()


def valid_cursor(value):
    return value is None or value in (START, END) or (value.isdigit() and len(value) <= 18)


def encode(event_id, event):
    """Official SDK SSE encoding, with an SSE id line for Last-Event-ID reconnects."""
    return f'id: {event_id}\n' + ENCODER.encode(event)
