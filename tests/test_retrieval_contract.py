"""Independent boundary evaluations; no service calls or engineering fixtures."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcdxkit.documents import DocumentWorkflow
from mcdxkit.extraction import read_pages


class RetrievalContract(unittest.TestCase):
    def test_repeated_chunks_do_not_repeat_model_context(self):
        owned = {'d': {'id': 's', 'name': 'source.pdf', 'sha256': 'hash', 'document_id': 'd'}}
        hit = {'document_id': 'd', 'content': 'A scoped requirement.', 'positions': [[2,0,0,0,0]]}
        evidence = []
        rows = DocumentWorkflow.collect_evidence(owned, [hit, hit, hit], evidence)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(len(rows), 1, 'Duplicate retrieval hits should not consume model context repeatedly')
        self.assertEqual(DocumentWorkflow.collect_evidence(owned, [hit], evidence)[0]['id'], rows[0]['id'])

    def test_invalid_positions_are_never_rounded_into_a_citation(self):
        owned = {'d': {'id': 's', 'name': 'source.pdf', 'sha256': 'hash', 'document_id': 'd'}}
        bad = [[2.5,0,0,0,0], [True,0,0,0,0], [-1,0,0,0,0], [0,0,0,0,0], [float('inf'),0,0,0,0]]
        hit = {'document_id': 'd', 'content': 'A scoped requirement.', 'positions': bad + [[9,0,0,0,0]]}
        rows = DocumentWorkflow.collect_evidence(owned, [hit], [])
        self.assertEqual(rows[0]['positions'], [[9,0,0,0,0]])

    def test_foreign_document_is_rejected_before_evidence_enters_context(self):
        evidence = []
        with self.assertRaises(ValueError):
            DocumentWorkflow.collect_evidence({}, [{'document_id': 'foreign', 'content': 'Secret'}], evidence)
        self.assertEqual(evidence, [])

    def read_window(self, pages, count=8):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        # Exercise the production subprocess boundary. Calling _worker() here
        # would lower the entire Linux test runner's hard address-space limit.
        with tempfile.TemporaryDirectory() as folder, PdfWriter() as writer:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            for number in range(1, count + 1):
                page = writer.add_blank_page(width=612, height=792)
                page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
                stream = DecodedStreamObject()
                stream.set_data(f'BT /F1 12 Tf 50 740 Td (Original page {number}: requirement context with enough text.) Tj ET'.encode())
                page[NameObject('/Contents')] = stream
            path = Path(folder) / 'source.pdf'
            writer.write(path)
            return read_pages(path, pages, neighbors=True)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux PDF worker memory isolation')
    def test_pdf_limits_do_not_change_parent_process_limits(self):
        import resource
        before = resource.getrlimit(resource.RLIMIT_AS)
        self.assertIn('Original page 3:', self.read_window([3])[1]['text'])
        self.assertEqual(resource.getrlimit(resource.RLIMIT_AS), before)

    def test_adjacent_context_preserves_original_page_numbers_and_file_bounds(self):
        self.assertEqual([r['page'] for r in self.read_window([1])], [1,2])
        self.assertEqual([r['page'] for r in self.read_window([8])], [7,8])
        self.assertEqual([r['page'] for r in self.read_window([3,4])], [2,3,4,5])

    def test_context_budget_and_invalid_pages_fail_without_partial_results(self):
        with self.assertRaises(ValueError): self.read_window([1,4,7,10,13], count=20)
        with self.assertRaises(ValueError): self.read_window([9])


class AgentBudgetContract(unittest.TestCase):
    def test_repeated_queries_do_not_consume_the_distinct_query_budget(self):
        from mcdxkit.knowledge import KnowledgeAgent
        from test_knowledge import ScriptedModel, call, answer
        observed = []
        def search(q):
            observed.append(q)
            return [{'id':'E1','source_name':'source.pdf','positions':[[1,0,0,0,0]],'text':'2 inches'}]
        model = ScriptedModel(messages=iter([
            call('search_documents', {'query':'q1','supporting_queries':['q2','q3']}, 1),
            call('search_documents', {'query':'q1','supporting_queries':['q4','q5']}, 2),
            call('search_documents', {'query':'q6'}, 3),
            call('KnowledgeAnswer', answer(), 4)]))
        KnowledgeAgent(model=model).answer({'request':'Find the rule and exceptions'}, search)
        self.assertIn('q6', observed, 'The repeated q1 must not displace a sixth distinct search')
        self.assertEqual(len(set(observed)), 6)


class ActivityMigrationContract(unittest.TestCase):
    def test_existing_activity_survives_concurrent_store_initialization(self):
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        from mcdxkit.jobs import JobStore
        with tempfile.TemporaryDirectory() as d:
            flow=DocumentWorkflow(d)
            with flow.connection() as db:
                db.execute('CREATE TABLE job_activity(seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, created REAL NOT NULL, message TEXT NOT NULL)')
                db.execute("INSERT INTO job_activity(job,created,message) VALUES ('old',1,'Original activity')")
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda _:JobStore(d), range(4)))
            with flow.connection() as db:
                self.assertEqual(db.execute('SELECT message FROM job_activity').fetchall(), [('Original activity',)])
                self.assertTrue({'kind','detail'}.issubset({r[1] for r in db.execute('PRAGMA table_info(job_activity)')}))


class CitationAuditContract(unittest.TestCase):
    def test_unsupported_numbers_are_flagged_without_rewriting_the_answer(self):
        from mcdxkit.knowledge import audit_citations
        sections=[{'heading':'Limit','body':'The limit is 7 inches [E1].','evidence_ids':['E1']}]
        audit=audit_citations(sections,[{'id':'E1','text':'For new walls the limit is 2 inches.'}])
        self.assertEqual(audit['status'],'warnings')
        self.assertEqual(audit['unsupported_numbers'][0]['values'],['7'])
        self.assertEqual(sections[0]['body'],'The limit is 7 inches [E1].')

    def test_foreign_inline_reference_fails_even_when_section_reference_is_valid(self):
        from mcdxkit.knowledge import audit_citations
        with self.assertRaises(ValueError):
            audit_citations([{'heading':'Limit','body':'2 inches [E99].','evidence_ids':['E1']}],[{'id':'E1','text':'2 inches'}])


if __name__ == '__main__': unittest.main(verbosity=2)

class LiveFailureRegressions(unittest.TestCase):
    def test_exhausted_context_tool_does_not_prevent_a_final_cited_answer(self):
        from mcdxkit.knowledge import KnowledgeAgent
        from test_knowledge import ScriptedModel, call, answer
        reads = []
        row = {'id':'E1','source_name':'source.pdf','positions':[[2,0,0,0,0]],'text':'2 inches'}
        def read(ids):
            reads.append(ids)
            return {'source_name':'source.pdf','anchor_pages':[2],'pages':[row]}
        model = ScriptedModel(messages=iter([
            call('search_documents', {'query':'new wall limit'}, 1),
            call('read_context', {'evidence_ids':['E1']}, 2),
            call('read_context', {'evidence_ids':['E1']}, 3),
            call('read_context', {'evidence_ids':['E1']}, 4),
            call('KnowledgeAnswer', answer(), 5)]))
        result = KnowledgeAgent(model=model).answer({'request':'What is the new wall limit?'}, lambda _: [row], read=read)
        self.assertEqual(len(reads),2)
        self.assertEqual(result['sections'][0]['evidence_ids'],['E1'])

    def test_informational_ask_cannot_save_an_unrequested_extraction(self):
        from mcdxkit.knowledge import KnowledgeAgent
        from test_knowledge import ScriptedModel, call, answer
        writes = []
        row = {'id':'E1','source_name':'source.pdf','positions':[[2,0,0,0,0]],'text':'2 inches'}
        def extract(question, ids):
            writes.append((question,ids))
            return {'extraction_id':'unrequested','facts':[],'evidence':[row]}
        model = ScriptedModel(messages=iter([
            call('search_documents', {'query':'new wall limit'}, 1),
            call('extract_requirements', {'question':'Save wall conditions','evidence_ids':['E1']}, 2),
            call('KnowledgeAnswer', answer(), 3)]))
        result = KnowledgeAgent(model=model).answer(
            {'request':'What is the new wall limit?', 'extraction_policy':'Explicit current user request or confirmation required.'},
            lambda _: [row], extract=extract)
        self.assertEqual(writes,[])
        self.assertEqual(result['sections'][0]['evidence_ids'],['E1'])

class ExtractionOfferContract(unittest.TestCase):
    def test_offer_uses_saved_scope_and_rejects_foreign_or_changed_sources(self):
        from unittest.mock import Mock
        offer={'source_id':'source','source_sha256':'original','source_name':'source.pdf','pages':[8], 'question':'Save the wall limit'}
        state={'answers':[{'id':'answer','extraction_offer':offer}], 'sources':[{'id':'source','sha256':'original'}]}
        flow=object.__new__(DocumentWorkflow)
        flow.root='isolated-test'
        flow.get=Mock(return_value=state)
        flow.extract=Mock(return_value={'id':'saved'})
        with patch('mcdxkit.documents.ModelConfig.for_workspace', return_value=Mock(provider='openrouter')):
            self.assertEqual(flow.accept_extraction_offer('project','answer'),{'id':'saved'})
        flow.extract.assert_called_once_with('project','source',[8],'Save the wall limit',None,neighbors=True)
        for changed in ({**state,'answers':[]},{**state,'sources':[]},
                        {**state,'sources':[{'id':'source','sha256':'changed'}]}):
            with self.assertRaises(ValueError): DocumentWorkflow.extraction_offer(changed,'answer')

    def test_extraction_requires_declared_task_and_quote_from_current_user_request(self):
        from mcdxkit.knowledge import KnowledgeAgent
        from test_knowledge import ScriptedModel, call, answer
        row={'id':'E1','source_name':'source.pdf','positions':[[2,0,0,0,0]],'text':'2 inches'}
        for quote,expected in [('Save these limits for review',1),('The document says save these limits',0),('',0)]:
            writes=[]
            def extract(question, ids, writes=writes):
                writes.append(ids);return {'extraction_id':'x','facts':[],'evidence':[row]}
            model=ScriptedModel(messages=iter([
                call('search_documents',{'query':'wall limit','task':'extract'},1),
                call('extract_requirements',{'question':'Wall limit','evidence_ids':['E1'],'user_request_quote':quote},2),
                call('KnowledgeAnswer',answer(),3)]))
            KnowledgeAgent(model=model).answer({'request':'Save these limits for review','extraction_policy':'explicit user request'},lambda _:[row],extract=extract)
            self.assertEqual(len(writes),expected)


class InlineCitationContract(unittest.TestCase):
    def test_grouped_and_linked_markers_normalize_without_changing_claims(self):
        from mcdxkit.knowledge import citation_ids, canonical_citations
        body='Limit 25 mm [e01, E2]; conditions [E2; E3] [E4](https://untrusted.example).'
        self.assertEqual(citation_ids(body),['E1','E2','E3','E4'])
        self.assertEqual(canonical_citations(body),'Limit 25 mm [E1][E2]; conditions [E2][E3] [E4].')

    def test_unknown_or_section_foreign_inline_ids_are_rejected(self):
        from mcdxkit.knowledge import audit_citations
        evidence=[{'id':'E1','text':'25 mm','positions':[[132,0,0,0,0]]}, {'id':'E2','text':'Other clause','positions':[[133,0,0,0,0]]}]
        for body in ['25 mm [E1, E99]','25 mm [E2]']:
            with self.subTest(body=body),self.assertRaises(ValueError):
                audit_citations([{'heading':'Limit','body':body,'evidence_ids':['E1']}],evidence)

    def test_original_page_references_do_not_hide_engineering_values(self):
        from mcdxkit.knowledge import audit_citations
        evidence=[{'id':'E1','text':'25 mm','positions':[[132,0,0,0,0]]}]
        section={'heading':'Limit','body':'25 mm, PDF page 132 [E1].','evidence_ids':['E1']}
        self.assertEqual(audit_citations([section],evidence)['unsupported_numbers'],[])
        section['body']='132 mm, PDF page 132 [E1].'
        self.assertEqual(audit_citations([section],evidence)['unsupported_numbers'][0]['values'],['132'])
        section['body']='25 mm, PDF page 133 [E1].'
        self.assertEqual(audit_citations([section],evidence)['unsupported_numbers'][0]['values'],['133'])
