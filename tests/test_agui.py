"""Validate the public activity projection independently of agent/model execution."""
import json
import unittest

from mcdxkit import agui


class AGUIProjectionTests(unittest.TestCase):
    def snapshot(self, state='running'):
        return {'id':'job', 'project':'project', 'action':'ask', 'state':state,
                'created':1, 'updated':2, 'uncertain':False,
                'activity':[{'seq':2, 'time':1.5, 'message':'Found evidence',
                             'type':'search', 'detail':{'passages':3}}]}

    def decoded(self, job, cursor=None):
        return [json.loads(agui.encode(i,e).split('data: ',1)[1])
                for i,e in agui.events(job, {'answers':[]}, cursor)]

    def test_lifecycle_order_and_official_serialization(self):
        for state, terminal in [('succeeded','RUN_FINISHED'), ('failed','RUN_ERROR'), ('cancelled','RUN_FINISHED')]:
            with self.subTest(state=state):
                job={**self.snapshot(state), 'error':'Provider interrupted', 'result':{'id':'answer','private':'excluded'}}
                events=self.decoded(job)
                self.assertEqual([e['type'] for e in events], ['RUN_STARTED','ACTIVITY_SNAPSHOT',terminal])
                self.assertEqual(events[1]['content']['passages'],3)
                self.assertNotIn('private', json.dumps(events))
                if state=='cancelled': self.assertEqual(events[-1]['outcome']['type'],'cancelled')

    def test_reconnect_does_not_duplicate_already_seen_activity_or_terminal(self):
        job=self.snapshot()
        self.assertEqual([e['type'] for e in self.decoded(job,'run')],['ACTIVITY_SNAPSHOT'])
        self.assertEqual(self.decoded(job,'2'),[])
        self.assertEqual(self.decoded({**job,'state':'succeeded'},'end'),[])
        for invalid in ('bad', '2\ndata: fake', '1'*19): self.assertFalse(agui.valid_cursor(invalid))

    def test_uncertain_failure_is_reported_without_a_retry_command(self):
        events=self.decoded({**self.snapshot('failed'),'uncertain':True,'error':'Interrupted'})
        self.assertEqual(events[-1]['code'],'uncertain_remote_request')
        self.assertNotIn('retry', json.dumps(events))
