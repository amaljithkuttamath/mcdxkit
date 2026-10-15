"""Exercise the real agent graph with deterministic model/tool boundaries."""
import tempfile
import unittest
from pathlib import Path

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class ScriptedModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args, number):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': str(number)}])


def answer(ref='E1', status='supported'):
    return {'status': status, 'assessment': 'The retrieved specification directly addresses the requested limit.',
            'sections': [{'heading': 'Limit', 'body': 'The limit is 2 inches for new walls.', 'evidence_ids': [ref]}],
            'gaps': [], 'conflicts': []}


class KnowledgeTests(unittest.TestCase):
    def test_extraction_is_a_bounded_tool_with_current_source_citations(self):
        from mcdxkit.knowledge import KnowledgeAgent
        calls = []
        def extract(question, evidence_ids):
            calls.append((question, evidence_ids))
            return {'extraction_id': 'x', 'facts': [{'value_text': '2', 'unit': 'in'}],
                    'evidence': [{'id': 'E1', 'text': 'New walls: 2 inches.'}]}
        model = ScriptedModel(messages=iter([
            call('extract_requirements', {'question': 'New walls limit', 'evidence_ids': ['E1']}, 1),
            call('KnowledgeAnswer', answer(), 2)]))
        result = KnowledgeAgent(model=model).answer({'request': 'Extract the new wall limit'}, lambda _: [], extract=extract)
        self.assertEqual(calls, [('New walls limit', ['E1'])])
        self.assertEqual(result['sections'][0]['evidence_ids'], ['E1'])

    def test_workflow_steps_do_not_require_document_citations_but_research_does(self):
        from mcdxkit.knowledge import KnowledgeAnswer
        payload = {'kind': 'workflow', 'status': 'partial', 'assessment': 'Review the uploaded files.',
                   'sections': [{'heading': 'Next step', 'body': 'Choose the pages to extract.', 'evidence_ids': []}],
                   'gaps': [], 'conflicts': []}
        self.assertEqual(KnowledgeAnswer.model_validate(payload).kind, 'workflow')
        with self.assertRaises(ValueError): KnowledgeAnswer.model_validate({**payload, 'kind': 'research'})

    def test_invalid_structured_response_has_one_formatting_pass(self):
        import json
        from mcdxkit.knowledge import KnowledgeAgent
        model = ScriptedModel(messages=iter([
            call('inspect_project_workflow', {}, 1),
            call('KnowledgeAnswer', {'kind': 'workflow', 'assessment': 'Add your files.'}, 2),
            AIMessage(content=json.dumps({'kind': 'workflow', 'status': 'partial', 'assessment': 'Add your files.',
                                         'sections': [], 'gaps': [], 'conflicts': [], 'actions': ['add_files']}))]))
        self.assertEqual(KnowledgeAgent(model=model).answer({'request': 'Next?', 'workflow': {}}, lambda _: [])['actions'], ['add_files'])

    def test_workflow_stream_reports_actual_steps_and_requests_a_human_choice(self):
        from mcdxkit.knowledge import KnowledgeAgent
        activity = []
        payload = {'kind': 'workflow', 'status': 'partial', 'assessment': 'Choose what you want to do with the uploaded PDF.',
                   'sections': [], 'gaps': [], 'conflicts': [], 'actions': [],
                   'clarification': {'question': 'Do you want to search the document or extract a specific value?',
                                     'choices': ['Search the document', 'Extract a value']}}
        model = ScriptedModel(messages=iter([call('inspect_project_workflow', {}, 1), call('KnowledgeAnswer', payload, 2)]))
        result = KnowledgeAgent(model=model, progress=activity.append).answer({'request': 'Help with this file', 'workflow': {}}, lambda _: [])
        self.assertEqual(len(result['clarification']['choices']), 2)
        self.assertIn('Checking uploaded files and workflow state', activity)
        self.assertIn('Checking citations and available actions', activity)

    def test_agent_researches_again_and_only_returns_current_citations(self):
        from mcdxkit.knowledge import KnowledgeAgent
        queries = []
        def search(query):
            queries.append(query)
            return [] if len(queries) == 1 else [{'id': 'E1', 'text': 'New walls: 2 inches.'}]
        model = ScriptedModel(messages=iter([
            call('search_documents', {'query': 'wall settlement'}, 1),
            call('search_documents', {'query': 'new retaining walls settlement limit'}, 2),
            call('KnowledgeAnswer', answer(), 3)]))
        result = KnowledgeAgent(model=model).answer({'request': 'What is the limit?', 'history': []}, search)
        self.assertEqual(len(queries), 2)
        self.assertEqual(result['sections'][0]['evidence_ids'], ['E1'])

    def test_plain_text_research_is_formatted_once_and_still_validated(self):
        import json
        from mcdxkit.knowledge import KnowledgeAgent
        model = ScriptedModel(messages=iter([
            call('search_documents', {'query': 'new walls'}, 1),
            AIMessage(content='New walls have a 2 inch limit [E1].'),
            AIMessage(content=json.dumps(answer()))]))
        result = KnowledgeAgent(model=model).answer({'request': 'Limit?'}, lambda _: [{'id': 'E1', 'text': 'New walls: 2 inches.'}])
        self.assertEqual(result['sections'][0]['evidence_ids'], ['E1'])

    def test_empty_evidence_cannot_be_answered_from_model_memory(self):
        from mcdxkit.knowledge import KnowledgeAgent
        model = ScriptedModel(messages=iter([call('KnowledgeAnswer', answer(), 1)]))
        with self.assertRaisesRegex(ValueError, 'evidence|citation'):
            KnowledgeAgent(model=model).answer({'request': 'Limit?'}, lambda _: [])

    def test_runaway_search_is_bounded_and_does_not_retry(self):
        from mcdxkit.knowledge import KnowledgeAgent
        queries = []
        model = ScriptedModel(messages=iter([call('search_documents', {'query': 'limit'}, i) for i in range(8)]))
        with self.assertRaisesRegex(ValueError, 'limit'):
            KnowledgeAgent(model=model).answer({'request': 'Limit?'}, lambda q: queries.append(q) or [])
        self.assertEqual(len(queries), 3)

    def test_insufficient_evidence_is_a_saved_answer_not_an_invented_claim(self):
        from mcdxkit.knowledge import KnowledgeAgent
        payload = {'status': 'insufficient', 'assessment': 'No project passage supports this answer.',
                   'sections': [], 'gaps': ['Upload the missing addendum.'], 'conflicts': []}
        model = ScriptedModel(messages=iter([call('search_documents', {'query': 'addendum'}, 1), call('KnowledgeAnswer', payload, 2)]))
        self.assertEqual(KnowledgeAgent(model=model).answer({'request': 'Addendum?'}, lambda _: [])['status'], 'insufficient')

    def test_workflow_guidance_uses_uploaded_files_without_requiring_an_index(self):
        from mcdxkit.documents import DocumentWorkflow
        with tempfile.TemporaryDirectory() as directory:
            flow = DocumentWorkflow(directory); pid = flow.create('Synthetic workflow')['id']
            source = flow.add_source(pid, 'requirements.pdf', b'%PDF-1.4\nsynthetic')
            class Agent:
                model = 'test:free'
                def answer(self, context, search, **kwargs):
                    self.context = context
                    return {'kind': 'workflow', 'status': 'partial', 'assessment': 'Index the uploaded document to ask about it.',
                            'sections': [], 'gaps': [], 'conflicts': [], 'actions': ['index_documents']}
            agent = Agent()
            result = flow.ask(pid, 'I uploaded a document; what is next?', agent=agent)
            self.assertEqual(agent.context['workflow']['sources'][0]['id'], source['id'])
            self.assertEqual(result['actions'], ['index_documents'])
            self.assertFalse(result['workflow_state']['calculation_connected'])
            self.assertEqual(result['content']['suggestions'], [])

    def test_human_reply_preserves_the_pending_question_in_conversation_context(self):
        from mcdxkit.documents import DocumentWorkflow
        state = {'answers': [{'id': 'a', 'request': 'Help with my file', 'content': {'sections': []},
                             'assessment': {'explanation': 'Choose your next step.'},
                             'clarification': {'question': 'Search or extract?', 'choices': ['Search', 'Extract']}}]}
        history = DocumentWorkflow.conversation_history(state, 'a')
        self.assertEqual(history[0]['clarification']['question'], 'Search or extract?')
        self.assertEqual(history[0]['answer'], 'Choose your next step.')

    def test_agent_cannot_propose_unavailable_or_arbitrary_workflow_actions(self):
        from mcdxkit.documents import DocumentWorkflow
        with tempfile.TemporaryDirectory() as directory:
            flow = DocumentWorkflow(directory); pid = flow.create('No calculation')['id']
            class Agent:
                model = 'test:free'
                def answer(self, context, search, **kwargs):
                    return {'kind': 'workflow', 'status': 'partial', 'assessment': 'Run it.',
                            'sections': [], 'gaps': [], 'conflicts': [], 'actions': ['calculate']}
            with self.assertRaisesRegex(ValueError, 'action|calculation'):
                flow.ask(pid, 'What next?', agent=Agent())

    def test_conversation_retrieves_with_context_but_history_is_not_evidence(self):
        from mcdxkit.documents import DocumentWorkflow
        with tempfile.TemporaryDirectory() as directory:
            flow = DocumentWorkflow(Path(directory)); pid = flow.create('Synthetic scope')['id']
            source = flow.add_source(pid, 'walls.pdf', b'%PDF-1.4\nsynthetic')
            flow.add_source(pid, 'unindexed.pdf', b'%PDF-1.4\nnot indexed')
            with flow.edit(pid) as state:
                state['dataset_id'] = 'dataset'; state['sources'][0].update(status='ready', document_id='d1')
            contexts = []
            class Retrieval:
                def retrieve(self, dataset, ids, question):
                    if ids != ['d1']: raise AssertionError('Unready documents were searched')
                    return [{'document_id': 'd1', 'content': 'New walls: 2 inches.', 'positions': [[7, 0, 1, 0, 1]]}]
            class Agent:
                model = 'test:free'
                def answer(self, context, search, **kwargs):
                    contexts.append(context); search(context['request']); return answer()
            first = flow.ask(pid, 'Limits?', Retrieval(), agent=Agent())
            second = flow.ask(pid, 'Does that include existing walls?', Retrieval(), agent=Agent(), parent=first['id'])
            self.assertEqual(second['conversation_id'], first['id'])
            self.assertEqual(contexts[-1]['history'][0]['request'], 'Limits?')
            self.assertEqual(second['coverage']['ready'], 1)
            self.assertEqual(second['coverage']['total'], 2)
            self.assertEqual(second['evidence'][0]['source_id'], source['id'])
            self.assertTrue(second['content']['gaps'])
            with self.assertRaisesRegex(ValueError, 'conversation|answer'):
                flow.ask(pid, 'Foreign?', Retrieval(), agent=Agent(), parent='f'*32)

    def test_unconfirmed_statements_stay_private_and_feed_a_recheck(self):
        import json
        from mcdxkit.documents import DocumentWorkflow
        with tempfile.TemporaryDirectory() as directory:
            flow = DocumentWorkflow(Path(directory)); pid = flow.create('Recheck')['id']
            flow.add_source(pid, 'walls.pdf', b'%PDF-1.4\nsynthetic')
            with flow.edit(pid) as state:
                state['dataset_id'] = 'dataset'; state['sources'][0].update(status='ready', document_id='d1')
            class Retrieval:
                def retrieve(self, dataset, ids, question):
                    return [{'document_id': 'd1', 'content': 'New walls: 2 inches.', 'positions': [[7, 0, 1, 0, 1]]}]
            contexts = []
            class Agent:
                model = 'test:free'
                def answer(self, context, search, **kwargs):
                    contexts.append(context); search(context['request'])
                    return {'status': 'supported', 'assessment': 'Limits found.', 'gaps': [], 'conflicts': [],
                            'sections': [{'heading': 'Limit', 'evidence_ids': ['E1'],
                                          'body': 'New walls: 2 inches [E1]. Existing walls: 5 inches [E1].'}]}
                def verify(self, statements, evidence):
                    # A lying verifier: MCDXKit's own number check must still reject the 5-inch statement.
                    return {'checks': [{'id': s['id'], 'verdict': 'supported', 'evidence_ids': ['E1'],
                                        'quotes': [{'evidence_id': 'E1', 'text': 'New walls: 2 inches.'}]} for s in statements]}
            first = flow.ask(pid, 'Wall limits?', Retrieval(), agent=Agent())
            self.assertEqual(first['content']['sections'][0]['body'], 'New walls: 2 inches [E1].')
            self.assertEqual(first['grounding']['unconfirmed_statements'], 1)
            self.assertNotIn('_unconfirmed', first['grounding'])
            # Never in project state, so never in the browser, exports or audit receipts.
            self.assertNotIn('Existing walls', json.dumps(flow.public(flow.get(pid))))
            self.assertEqual(flow.unconfirmed(pid, first['id'])[0]['text'], 'Existing walls: 5 inches [E1].')
            # A recheck is a follow-up: the model gets the parent's unconfirmed statements as private context.
            second = flow.ask(pid, 'Recheck the statements from your previous answer that could not be confirmed.',
                              Retrieval(), agent=Agent(), parent=first['id'])
            turn = contexts[-1]['history'][-1]
            self.assertEqual(turn['unconfirmed_statements'][0]['text'], 'Existing walls: 5 inches [E1].')
            self.assertIn('a number is not in the quoted text', turn['unconfirmed_statements'][0]['reasons'])
            self.assertIn('NOT confirmed', turn['unconfirmed_note'])
            self.assertNotIn('Existing walls', json.dumps(second['content']))
            self.assertFalse(contexts[0]['history'])      # a first question carries no earlier turn

    def test_tool_extraction_saves_unreviewed_facts_and_citations_without_input_changes(self):
        from unittest.mock import patch
        from mcdxkit.documents import DocumentWorkflow
        from test_extraction import FACT, PAGES
        with tempfile.TemporaryDirectory() as directory:
            flow = DocumentWorkflow(directory); pid = flow.create('Extraction tool')['id']
            source = flow.add_source(pid, 'walls.pdf', b'%PDF-1.4\nfixture')
            class Agent:
                model = 'test:free'
                def answer(self, context, search, *, extract):
                    search('new wall settlement limits')
                    result = extract('Extract wall limit', ['E1'])
                    self.result = result
                    return {**answer(result['evidence'][0]['id']), 'actions': ['review_requirements']}
            with flow.edit(pid) as state:
                state['dataset_id'] = 'dataset'; state['sources'][0].update(status='ready', document_id='d1')
            class Retrieval:
                def retrieve(self, *args): return [{'document_id': 'd1', 'content': PAGES[0]['text'], 'positions': [[1,0,0,0,0]]}]
            agent = Agent()
            with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-only-no-network'}), patch('mcdxkit.extraction.read_pages', return_value=PAGES), patch('mcdxkit.extraction.RequirementExtractor.extract', return_value=({'facts': [FACT], 'gaps': []}, {})):
                saved = flow.ask(pid, 'Extract wall limit', Retrieval(), agent=agent)
            state = flow.get(pid)
            self.assertEqual(saved['extraction_ids'], [state['extractions'][0]['id']])
            self.assertEqual(saved['evidence'][0]['source_id'], source['id'])
            self.assertEqual(saved['evidence'][0]['positions'][0][0], 1)
            self.assertIsNone(state['extractions'][0]['facts'][0]['review'])
            self.assertEqual(state['drafts'], [])
            self.assertEqual(state['runs'], [])
            self.assertEqual(saved['content']['suggestions'], [])

    def test_extraction_tool_never_reads_a_foreign_project_source(self):
        from unittest.mock import patch
        from mcdxkit.documents import DocumentWorkflow
        with tempfile.TemporaryDirectory() as directory:
            flow = DocumentWorkflow(directory); pid = flow.create('Selected')['id']; other = flow.create('Other')['id']
            source = flow.add_source(other, 'private.pdf', b'%PDF-1.4\nfixture')
            class Agent:
                model = 'test:free'
                def answer(self, context, search, *, extract):
                    return extract('Extract', [source['id']])
            with patch('mcdxkit.extraction.RequirementExtractor.extract') as provider:
                with self.assertRaisesRegex(ValueError, 'evidence not retrieved'):
                    flow.ask(pid, 'Extract', agent=Agent())
                provider.assert_not_called()
