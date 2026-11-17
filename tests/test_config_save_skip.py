import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'xxmi_launcher' / 'core' / 'config_manager.py'


def load_config_manager(writes):
    """ConfigManager from source with its Windows-only imports replaced by small stand-ins."""
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ConfigManager')

    def write_file(path, data):
        writes.append(path)
        Path(path).write_text(data, encoding='utf-8')

    namespace = {
        'Path': Path,
        'json': json,
        'log': SimpleNamespace(exception=lambda *a, **k: None),
        'Paths': SimpleNamespace(App=SimpleNamespace(write_file=write_file)),
        'AppConfigSecurity': lambda config: None,
        'AppConfig': object,
    }
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['ConfigManager']


class ConfigSaveSkipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'XXMI Launcher Config.json'
        self.writes = []
        manager_class = load_config_manager(self.writes)
        path = self.path

        class Manager(manager_class):
            config_path = path

            @classmethod
            def config_to_json(cls, config):
                return json.dumps(config, indent=4)

        self.manager = Manager({'Launcher': {'theme': 'Default'}})

    def test_unchanged_configuration_is_not_rewritten(self):
        self.manager.save()
        self.manager.save()
        self.assertEqual(len(self.writes), 1)

    def test_changed_configuration_is_written(self):
        self.manager.save()
        self.manager.config['Launcher']['theme'] = 'Other'
        self.manager.save()
        self.assertEqual(len(self.writes), 2)
        self.assertIn('Other', self.path.read_text(encoding='utf-8'))

    def test_missing_or_different_file_is_written(self):
        self.path.write_text('{"edited": "by hand"}', encoding='utf-8')
        self.manager.save()
        self.assertEqual(len(self.writes), 1)
        self.path.unlink()
        self.manager.save()
        self.assertEqual(len(self.writes), 2)

    def test_file_matches_compares_exact_bytes(self):
        self.path.write_bytes(b'abc')
        self.assertTrue(self.manager._file_matches(self.path, b'abc'))
        self.assertFalse(self.manager._file_matches(self.path, b'abd'))
        self.assertFalse(self.manager._file_matches(self.path.with_name('missing'), b'abc'))


if __name__ == '__main__':
    unittest.main()
