"""Job persistence and publication boundaries, independent of an open browser."""
import tempfile
import unittest
import time
from pathlib import Path

from mcdxkit.documents import DocumentWorkflow


class JobTests(unittest.TestCase):
    def test_activity_is_durable_bounded_and_does_not_expose_owner_or_payload(self):
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Next?'}, 'progress')
        token = store.claim(job['id'])
        store.stage(job['id'], token, 'Checking uploaded files')
        store.stage(job['id'], token, 'Checking uploaded files')
        store.stage(job['id'], token, 'Checking citations')
        public = self.store().listing(self.project)[0]
        self.assertEqual([item['message'] for item in public['activity']][-2:], ['Checking uploaded files', 'Checking citations'])
        self.assertNotIn('owner', public)
        self.assertNotIn('payload', public)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.flow = DocumentWorkflow(self.root)
        self.project = self.flow.create('Job example')['id']

    def store(self):
        from mcdxkit.jobs import JobStore
        return JobStore(self.root)

    def test_request_identity_survives_restart_and_conflicting_reuse_is_rejected(self):
        store = self.store()
        job = store.submit(self.project, 'ask', {'question': 'What is the limit?'}, 'first')
        again = self.store().submit(self.project, 'ask', {'question': 'What is the limit?'}, 'first')
        self.assertEqual(job['id'], again['id'])
        self.assertEqual(again['state'], 'queued')
        with self.assertRaisesRegex(ValueError, 'different'):
            store.submit(self.project, 'ask', {'question': 'Different question'}, 'first')
        with self.assertRaises(ValueError): store.submit(self.project, 'shell', {}, 'bad')

    def test_cancel_and_duplicate_delivery_cannot_publish(self):
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        token = store.claim(job['id'])
        self.assertIsNotNone(token)
        self.assertIsNone(store.claim(job['id']))
        store.cancel(job['id'])
        with self.assertRaisesRegex(ValueError, 'cancel|active'):
            store.complete(job['id'], token, {'id': 'result'})
        self.assertEqual(store.get(job['id'])['state'], 'cancelled')
        self.assertIsNone(store.get(job['id'])['result'])

    def test_interrupted_running_job_is_uncertain_and_never_automatically_requeued(self):
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        quiet = store.submit(self.project, 'ask', {'question': 'Other limit?'}, 'second')
        token = store.claim(job['id']); store.claim(quiet['id'])
        store.mark_remote(job['id'], token)          # this worker had opened a connection; the other had not
        with self.flow.connection() as db:
            db.execute('UPDATE jobs SET updated=0')
        store.recover()
        restored = store.get(job['id'])
        self.assertEqual(restored['state'], 'failed')
        self.assertTrue(restored['uncertain'])
        self.assertEqual((store.get(quiet['id'])['state'], store.get(quiet['id'])['uncertain']), ('failed', False))
        self.assertIsNone(store.claim(job['id']))
        with self.assertRaises(ValueError): store.complete(job['id'], token, {})

    def execute(self, job):
        from mcdxkit.jobs import execute
        store = self.store(); token = store.claim(job['id'])
        execute(str(self.root), job['id'], token)
        return store.get(job['id'])

    def test_failure_before_any_connection_is_not_uncertain(self):
        from unittest.mock import patch
        from mcdxkit.document_clients import ModelConfig
        empty = ModelConfig('', '', '', '', 'test/model:free')
        store = self.store()
        with patch.object(ModelConfig, 'for_workspace', return_value=empty):
            # Missing service configuration fails before any request, and says where to fix it.
            failed = self.execute(store.submit(self.project, 'ask', {'question': 'Limit?'}, 'unconfigured'))
            self.assertEqual((failed['state'], failed['uncertain']), ('failed', False))
            self.assertIn('open Services', failed['error'])
            # A job made stale after submission is rejected before any request too.
            stale = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'stale')
            self.flow.add_source(self.project, 'new.pdf', b'%PDF-1.4\nnew source')
            failed = self.execute(stale)
        self.assertEqual((failed['state'], failed['uncertain']), ('failed', False))
        self.assertIn('changed', failed['error'])

    def test_failure_after_a_connection_is_uncertain(self):
        import socket
        from unittest.mock import patch
        from mcdxkit.document_clients import ModelConfig
        server = socket.create_server(('127.0.0.1', 0)); self.addCleanup(server.close)
        def reach_service(*args, **kwargs):
            with socket.create_connection(server.getsockname()): pass
            raise ValueError('Model service returned HTTP 502.')
        config = ModelConfig('', '', '', '', 'test/model:free')
        with patch.object(ModelConfig, 'for_workspace', return_value=config), patch.object(DocumentWorkflow, 'ask', reach_service):
            job = self.store().submit(self.project, 'ask', {'question': 'Limit?'}, 'remote')
            failed = self.execute(job)
        self.assertEqual((failed['state'], failed['uncertain']), ('failed', True))
        # A later job in the same process starts unrecorded: only its own connections count.
        with patch.object(ModelConfig, 'for_workspace', return_value=config):
            later = self.execute(self.store().submit(self.project, 'ask', {'question': 'Other?'}, 'after'))
        self.assertFalse(later['uncertain'])

    def test_cancelling_counts_only_a_job_that_connected(self):
        store = self.store()
        quiet = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'quiet')
        busy = store.submit(self.project, 'ask', {'question': 'Other?'}, 'busy')
        store.claim(quiet['id']); store.mark_remote(busy['id'], store.claim(busy['id']))
        self.assertFalse(store.cancel(quiet['id'])['uncertain'])
        self.assertTrue(store.cancel(busy['id'])['uncertain'])

    def test_source_change_after_submission_rejects_execution(self):
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        self.flow.add_source(self.project, 'new.pdf', b'%PDF-1.4\nnew source')
        token = store.claim(job['id'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            store.validate_snapshot(job['id'], token)

    def test_model_configuration_change_rejects_queued_research(self):
        from unittest.mock import patch
        from mcdxkit.document_clients import ModelConfig
        first = ModelConfig('', '', '', '', 'test/first:free')
        second = ModelConfig('', '', '', '', 'test/changed:free')
        store = self.store()
        with patch.object(ModelConfig, 'for_workspace', return_value=first):
            job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        token = store.claim(job['id'])
        with patch.object(ModelConfig, 'for_workspace', return_value=second), self.assertRaisesRegex(ValueError, 'changed'):
            store.validate_snapshot(job['id'], token)

    def test_shared_worker_rejects_queued_chatgpt_job_before_claim_or_network(self):
        from unittest.mock import patch
        from mcdxkit.document_clients import ModelConfig
        from mcdxkit.chatgpt import DISABLED_ENV
        config=ModelConfig('', '', '', '', 'test:free', provider='chatgpt', chatgpt_model='test-model')
        store=self.store()
        with patch.object(ModelConfig,'for_workspace',return_value=config),patch.object(DocumentWorkflow,'chatgpt_account',return_value='synthetic-account'):
            job=store.submit(self.project,'ask',{'question':'Limit?'},'queued-chatgpt')
            with patch.dict('os.environ',{DISABLED_ENV:'1'}),patch('mcdxkit.chatgpt.ChatGPTAccounts.access_token') as access:
                self.assertTrue(store.reject_shared_chatgpt(job['id']))
                access.assert_not_called()
        self.assertEqual(store.get(job['id'])['state'],'failed')
        self.assertFalse(store.get(job['id'])['uncertain'])
        self.assertIsNone(store.claim(job['id']))

    def test_foreign_project_cannot_read_job_and_queue_cancel_never_runs(self):
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        other = self.flow.create('Other project')['id']
        with self.assertRaises(ValueError): store.get(job['id'], project=other)
        store.cancel(job['id']); self.assertIsNone(store.claim(job['id']))

    def test_domain_record_and_job_success_publish_in_one_transaction(self):
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        token = store.claim(job['id']); record = {'id': 'answer-1'}
        flow = store.workflow(job['id'], token)
        with flow.edit(self.project, result=record) as state:
            state['answers'].append(record)
        self.assertEqual(self.flow.get(self.project)['answers'], [record])
        self.assertEqual(store.get(job['id'])['result'], record)
        self.assertEqual(store.get(job['id'])['state'], 'succeeded')

    def test_real_worker_reports_failure_without_blocking_read_access(self):
        from mcdxkit.jobs import start_worker
        store = self.store()
        job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        self.flow.add_source(self.project, 'changed.pdf', b'%PDF-1.4\nchanged after enqueue')
        worker = start_worker(self.root, channel='chat')
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and store.get(job['id'])['state'] not in ('failed', 'succeeded'):
                self.assertEqual(self.flow.get(self.project)['title'], 'Job example')
                time.sleep(0.05)
            restored = store.get(job['id'])
            self.assertEqual(restored['state'], 'failed', restored)
            self.assertIn('changed', restored['error'])
        finally:
            worker.terminate(); worker.wait(timeout=8)

    def test_cancelling_running_operation_terminates_the_owned_process(self):
        import subprocess
        import sys
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from mcdxkit.jobs import supervise
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Next?'}, 'cancel-child')
        real_popen = subprocess.Popen; launched = threading.Event(); children = []
        def launch(*args, **kwargs):
            child = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
            children.append(child); launched.set(); return child
        with patch('mcdxkit.jobs.subprocess.Popen', side_effect=launch), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(supervise, self.root, job['id'])
            try:
                self.assertTrue(launched.wait(5))
                self.assertEqual(store.get(job['id'])['state'], 'running')
                store.cancel(job['id']); future.result(timeout=8)
                self.assertIsNotNone(children[0].poll())
                self.assertEqual(store.get(job['id'])['state'], 'cancelled')
                self.assertIsNone(store.get(job['id'])['result'])
            finally:
                for child in children:
                    if child.poll() is None: child.kill(); child.wait(timeout=5)

    def test_queue_messages_are_json_and_do_not_deserialize_pickle(self):
        from mcdxkit.jobs import queue, JobSerializer
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        huey, _ = queue(self.root, 'chat')
        task = huey.dequeue()
        self.assertEqual(task.args, [job['id']])
        with self.assertRaises(ValueError): JobSerializer().deserialize(b'not-json-pickle')

    def test_chat_dispatch_has_a_separate_lane_from_calculation_work(self):
        from mcdxkit.jobs import queue
        store = self.store(); job = store.submit(self.project, 'ask', {'question': 'Limit?'}, 'first')
        batch, _ = queue(self.root, 'batch')
        chat, _ = queue(self.root, 'chat')
        self.assertIsNone(batch.dequeue())
        self.assertEqual(chat.dequeue().args, [job['id']])
