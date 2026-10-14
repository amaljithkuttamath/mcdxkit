import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('setup_ragflow', Path(__file__).parents[1] / 'scripts' / 'setup_ragflow.py')


class SetupTests(unittest.TestCase):
    def test_prepared_stack_hides_dependency_ports_and_shares_only_api_network(self):
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        source = {'services': {'ragflow-cpu': {'ports': [{'published': '80', 'target': 80}], 'networks': {'ragflow': None}, 'profiles': ['cpu']},
                               'mysql': {'ports': [{'published': '3306', 'target': 3306}], 'networks': {'ragflow': None}, 'profiles': ['metadata-mysql']}},
                  'networks': {'ragflow': {'name': 'upstream_ragflow'}}, 'volumes': {'database': {'name': 'upstream_database'}}}
        value = module.private_config(source)
        self.assertEqual(value['services']['mysql']['ports'], [])
        self.assertNotIn('knowledge', value['services']['mysql']['networks'])
        self.assertIn('knowledge', value['services']['ragflow-cpu']['networks'])
        self.assertTrue(all(p['host_ip'] == '127.0.0.1' for p in value['services']['ragflow-cpu']['ports']))
        self.assertNotIn('profiles', value['services']['ragflow-cpu'])
        self.assertEqual(value['volumes']['database']['name'], 'mcdxkit-ragflow_database')
