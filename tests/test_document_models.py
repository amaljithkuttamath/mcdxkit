"""Model discovery never exposes keys or enables paid/non-text models."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcdxkit.document_clients import HTTPClient, ModelConfig, save_configuration


def model(identifier='example/model:free', **changes):
    return {'id': identifier, 'name': identifier, 'architecture': {'output_modalities': ['text']},
            'pricing': {'prompt': '0', 'completion': '0'}, 'context_length': 32000, **changes}


class ModelDiscoveryTests(unittest.TestCase):
    def test_authenticated_catalog_filters_and_sorts_without_saving_new_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            config = ModelConfig.for_workspace(folder)
            catalog = {'data': [model('z/model:free'), model('a/model:free'), model('paid/model'),
                model('cost/model:free', pricing={'prompt': '0.1', 'completion': '0'}),
                model('image/model:free', architecture={'output_modalities': ['image']}),
                model('missing/model:free', pricing={}), model('bad/model:free', pricing={'prompt': 'NaN', 'completion': '0'}),
                model('z/model:free'), None]}
            with patch.object(HTTPClient, 'request', return_value=catalog) as request:
                result = config.available_models('new-private-key')
            request.assert_called_once_with('GET', '/models/user')
            self.assertEqual([m['id'] for m in result['models']], ['a/model:free', 'z/model:free'])
            self.assertNotIn('private-key', json.dumps(result))
            self.assertFalse((Path(folder) / '.credentials.json').exists())

    def test_saved_key_empty_catalog_and_errors(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            save_configuration(folder, {'router_key': 'saved-key'})
            config = ModelConfig.for_workspace(folder)
            seen = []
            def request(client, method, path):
                seen.append(client.key)
                return {'data': []}
            with patch.object(HTTPClient, 'request', request):
                self.assertEqual(config.available_models()['models'], [])
            self.assertEqual(seen, ['saved-key'])
            with patch.object(HTTPClient, 'request', return_value={'error': 'upstream secret'}):
                with self.assertRaisesRegex(ValueError, 'catalog'): config.available_models()
            with patch.object(HTTPClient, 'request', side_effect=ValueError('HTTP 401')):
                with self.assertRaisesRegex(ValueError, '401'): config.available_models()
            for key in ('a\nb', 'a b', 5):
                with self.subTest(key=key), self.assertRaises(ValueError): config.available_models(key)
