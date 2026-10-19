"""CLI and browser operate on the same durable workflow records."""
import contextlib
import io
import json
import tempfile
import unittest

from mcdxkit.document_cli import main


class DocumentCLITests(unittest.TestCase):
    def test_create_submit_list_cancel_and_read_without_a_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            def run(*arguments):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(['--workspace', directory, *arguments]), 0)
                return json.loads(output.getvalue())
            project = run('create', '--title', 'Synthetic CLI project')['id']
            job = run('submit', project, 'ask', '--payload', '{"question":"What files do I need?"}', '--request-key', 'cli-1')
            repeated = run('submit', project, 'ask', '--payload', '{"question":"What files do I need?"}', '--request-key', 'cli-1')
            self.assertEqual(job['id'], repeated['id'])
            self.assertEqual(run('jobs', project)[0]['state'], 'queued')
            self.assertEqual(run('cancel', job['id'])['state'], 'cancelled')
            self.assertEqual(run('job', job['id'])['state'], 'cancelled')
            self.assertEqual(run('show', project)['answers'], [])

    def test_help_exposes_workflow_and_followup_commands(self):
        for command, expected in (([], 'requirement-draft'), (['requirement-draft'], '--applicability'), (['ask'], '--parent'), (['submit'], '--request-key')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exit:
                main([*command, '--help'])
            self.assertEqual(exit.exception.code, 0)
            self.assertIn(expected, output.getvalue())

    def test_requirement_draft_cli_passes_explicit_review_and_binding(self):
        from unittest.mock import patch
        bindings = [{'requirement': 'r', 'variable': 'limit', 'value': 2, 'unit': 'in'}]
        with patch('mcdxkit.document_cli.DocumentWorkflow') as workflow, contextlib.redirect_stdout(io.StringIO()):
            workflow.return_value.requirement_draft.return_value = {'id': 'draft'}
            self.assertEqual(main(['requirement-draft', 'p', '--requirements', 'r', '--bindings', json.dumps(bindings),
                                   '--reviewer', 'Engineer', '--note', 'Verified source', '--applicability', 'New walls']), 0)
        workflow.return_value.requirement_draft.assert_called_once_with('p', ['r'], bindings, 'Engineer', 'Verified source', 'New walls', None)
