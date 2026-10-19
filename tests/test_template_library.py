"""Workspace template library: upload once, reuse across projects, never overwrite or delete."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from mcdxkit.documents import DocumentWorkflow
from mcdxkit.template_library import TemplateLibrary
from test_pipeline import report, template
from test_server import ServerTests
from test_template_inputs import custom_template


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = TemplateLibrary(self.root / 'workspace')
        template(self.root / 'a.mcdx'); custom_template(self.root / 'b.mcdx')
        self.a, self.b = (self.root / 'a.mcdx').read_bytes(), (self.root / 'b.mcdx').read_bytes()

    def test_identical_bytes_deduplicate_and_a_changed_file_is_a_new_version(self):
        first = self.library.add('pile-cap.mcdx', self.a)
        self.assertEqual(first['id'], hashlib.sha256(self.a).hexdigest())
        self.assertEqual(self.library.add('renamed.mcdx', self.a)['id'], first['id'])
        second = self.library.add('pile-cap.mcdx', self.b)
        rows = self.library.listing()
        self.assertEqual([(r['name'], r['version']) for r in rows], [('pile-cap.mcdx', 2), ('pile-cap.mcdx', 1)])
        self.library.archive(first['id'])
        self.assertEqual([(r['name'], r['version']) for r in self.library.listing()], [('pile-cap.mcdx', 2)])
        self.assertEqual(self.library.path(first['id']).read_bytes(), self.a)
        self.assertEqual(self.library.path(second['id']).read_bytes(), self.b)
        # Discovered inputs are recorded from the template itself; custom names are not mapped by guessing.
        inputs = {i['variable']: i for i in second['inputs']}
        self.assertIn('AxialCustom', inputs)
        self.assertIsNone(inputs['AxialCustom']['component'])

    def test_archive_hides_without_deleting_and_reupload_restores(self):
        record = self.library.add('pile-cap.mcdx', self.a)
        self.library.archive(record['id'])
        self.assertEqual(self.library.listing(), [])
        self.assertEqual(len(self.library.listing(include_archived=True)), 1)
        self.assertEqual(self.library.path(record['id']).read_bytes(), self.a)
        self.assertFalse(self.library.add('pile-cap.mcdx', self.a)['archived'])

    def test_invalid_names_packages_and_tampering_fail_closed(self):
        for name in ('../x.mcdx', 'x.txt', '.mcdx', 'a/b.mcdx'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.library.add(name, self.a)
        for broken in (b'not a zip', b'PK\x03\x04 truncated'):
            with self.subTest(broken=broken), self.assertRaisesRegex(ValueError, 'Mathcad'):
                self.library.add('broken.mcdx', broken)
        self.assertEqual(self.library.listing(include_archived=True), [])
        self.assertEqual([p.name for p in (self.root / 'workspace').iterdir() if p.name.startswith('.template-')], [])
        record = self.library.add('pile-cap.mcdx', self.a)
        (self.root / 'workspace' / 'templates' / record['id'] / 'pile-cap.mcdx').write_bytes(self.b)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.library.path(record['id'])
        with self.assertRaises(ValueError):
            self.library.get('../' + record['id'])

    def test_usage_counts_calculations_across_projects(self):
        record = self.library.add('pile-cap.mcdx', self.a)
        flow = DocumentWorkflow(self.root / 'workspace')
        raw = self.root / 'loads.txt'; raw.write_text(report())
        for title in ('Pier 1', 'Pier 2'):
            pid = flow.create(title)['id']
            flow.attach_calculation(pid, raw, self.library.path(record['id']), cases=[1, 7], template_id=record['id'])
        listed = self.library.listing(usage=flow.template_usage())
        self.assertEqual(listed[0]['calculations'], 2)


class LibraryServerTests(ServerTests):
    """Reuses the server fixture; only the tests below run for this class."""

    def test_library_template_is_viewable_and_connects_as_a_calculation(self):
        status, record = self.request('POST', '/api/templates/upload?' + urlencode({'name': 'pile-cap.mcdx'}), self.template.read_bytes())
        self.assertEqual(status, 200, record)
        status, listing = self.request('GET', '/api/templates')
        self.assertEqual((status, [t['id'] for t in listing['templates']]), (200, [record['id']]))
        status, used = self.request('POST', f"/api/templates/{record['id']}/use", {})
        self.assertEqual((status, used['name']), (200, 'pile-cap.mcdx'))
        self.assertEqual(self.request('POST', '/api/view', {'id': used['id']})[0], 200)
        _, project = self.request('POST', '/api/documents/create', {'title': 'Pier 3'})
        _, raw = self.upload()
        status, linked = self.request('POST', '/api/documents/connect',
                                      {'project': project['id'], 'id': raw['id'], 'library_template': record['id'], 'cases': [1, 7]})
        self.assertEqual(status, 200, linked)
        self.assertEqual((linked['calculation']['template_id'], linked['calculation']['template_name']), (record['id'], 'pile-cap.mcdx'))
        _, listing = self.request('GET', '/api/templates')
        self.assertEqual(listing['templates'][0]['calculations'], 1)
        # The generated Mathcad file a run delivers is viewable after its package is verified.
        from mcdxkit.documents import DocumentWorkflow
        flow = DocumentWorkflow(self.root / 'results' / '.documents')
        run = flow.build(project['id'], None)
        status, delivered = self.request('POST', '/api/documents/run-file', {'project': project['id'], 'run': run['id']})
        self.assertEqual((status, delivered['name']), (200, 'calculation.mcdx'))
        status, view = self.request('POST', '/api/view', {'id': delivered['id']})
        self.assertEqual(status, 200, view)
        self.assertGreater(len(view['regions']), 0)
        self.assertEqual(self.request('POST', '/api/documents/run-file', {'project': project['id'], 'run': '0' * 32})[0], 400)
        (flow.root / project['id'] / 'runs' / run['id'] / 'package.zip').write_bytes(b'tampered')
        self.assertEqual(self.request('POST', '/api/documents/run-file', {'project': project['id'], 'run': run['id']})[0], 400)
        self.assertEqual(self.request('POST', f"/api/templates/{'0' * 64}/use", {})[0], 400)
        self.assertEqual(self.request('POST', f"/api/templates/{record['id']}/delete", {})[0], 404)
        self.assertEqual(self.request('GET', '/api/templates', authenticated=False)[0], 403)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(LibraryTests))
    suite.addTest(LibraryServerTests('test_library_template_is_viewable_and_connects_as_a_calculation'))
    return suite


if __name__ == '__main__':
    unittest.main()
