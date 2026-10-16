"""Document configuration reads a local dotenv file without executing it or exposing keys."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcdxkit.document_clients import DEFAULT_ANSWER_MODEL, ModelConfig, save_configuration


class DocumentEnvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous = Path.cwd()
        os.chdir(self.root)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        os.chdir(self.previous)
        self.temp.cleanup()

    def test_dotenv_supplies_key_and_model_without_mutating_environment(self):
        (self.root/'.env').write_text('export OPENROUTER_API_KEY="file-private"\nMCDXKIT_OPENROUTER_MODEL=example/model:free # chosen\n')
        config = ModelConfig.from_env()
        self.assertEqual(config.router_key, 'file-private')
        self.assertEqual(config.model, 'example/model:free')
        self.assertNotIn('OPENROUTER_API_KEY', os.environ)
        self.assertNotIn('file-private', json.dumps(config.public()))
        self.assertNotIn('file-private', repr(config))

    def test_environment_then_browser_settings_take_precedence(self):
        (self.root/'.env').write_text('OPENROUTER_API_KEY=file-private\n')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'process-private'}):
            self.assertEqual(ModelConfig.from_env().router_key,'process-private')
            save_configuration(self.root/'workspace', {'router_key':'browser-private'})
            self.assertEqual(ModelConfig.for_workspace(self.root/'workspace').router_key,'browser-private')

    def test_secret_file_and_explicit_process_key_override_dotenv_secret_file(self):
        (self.root/'key.txt').write_text('from-secret-file\n')
        (self.root/'.env').write_text('OPENROUTER_API_KEY_FILE=key.txt\n')
        self.assertEqual(ModelConfig.from_env().router_key,'from-secret-file')
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'process-private'}):
            self.assertEqual(ModelConfig.from_env().router_key,'process-private')

    def test_values_are_literal_and_parent_files_are_not_searched(self):
        (self.root/'.env').write_text('OPENROUTER_API_KEY=${UNRELATED}\nMCDXKIT_OPENROUTER_MODEL=$(touch executed)\n')
        with patch.dict(os.environ, {'UNRELATED':'do-not-expand'}):
            self.assertEqual(ModelConfig.from_env().router_key,'${UNRELATED}')
            self.assertEqual(ModelConfig.from_env().model,'$(touch executed)')
        self.assertFalse((self.root/'executed').exists())
        child=self.root/'child';child.mkdir();os.chdir(child)
        self.assertEqual(ModelConfig.from_env().router_key,'')

    def test_tested_answer_is_default_but_explicit_selection_is_preserved(self):
        self.assertEqual(ModelConfig.from_env().model, DEFAULT_ANSWER_MODEL)
        (self.root/'.env').write_text('MCDXKIT_OPENROUTER_MODEL=chosen/model:free\n')
        self.assertEqual(ModelConfig.from_env().model, 'chosen/model:free')
