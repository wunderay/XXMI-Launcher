import ast
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'xxmi_launcher' / 'core' / 'config_manager.py'
APPLICATION = ROOT / 'src' / 'xxmi_launcher' / 'core' / 'application.py'


def load_config_manager(writes, linesep=os.linesep):
    """ConfigManager from source with its Windows-only imports replaced by small stand-ins."""
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ConfigManager')

    def write_file(path, data):
        writes.append(path)
        # Text mode as Paths.App.write_file uses it, with the platform's line separator
        with open(path, 'w', encoding='utf-8', newline=linesep) as file:
            file.write(data)

    namespace = {
        'Path': Path,
        'json': json,
        'os': type('os', (), {'linesep': linesep}),
        'log': SimpleNamespace(exception=lambda *a, **k: None),
        'Paths': SimpleNamespace(App=SimpleNamespace(write_file=write_file)),
        'AppConfigSecurity': lambda config: None,
        'AppConfig': object,
    }
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['ConfigManager']


class ConfigSaveSkipTests(unittest.TestCase):
    linesep = os.linesep

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'XXMI Launcher Config.json'
        self.writes = []
        manager_class = load_config_manager(self.writes, self.linesep)
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


class WindowsLineEndingSaveSkipTests(ConfigSaveSkipTests):
    """Windows text mode stores CRLF; an unchanged configuration must still be recognised."""
    linesep = '\r\n'

    def test_file_holds_crlf(self):
        self.manager.save()
        self.assertIn(b'\r\n', self.path.read_bytes())


class ConfigBackupSkipTests(unittest.TestCase):
    """Application.load_config copies the loaded configuration to Backups only when it differs."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.config = root / 'XXMI Launcher Config.json'
        self.backup = root / 'Backups' / self.config.name
        self.backup.parent.mkdir()
        self.copies = []

        def copy_file(source, target):
            self.copies.append(target)
            Path(target).write_bytes(Path(source).read_bytes())

        manager_class = load_config_manager([])
        manager = SimpleNamespace(config_path=self.config, load=lambda *a: None,
                                  _file_matches=manager_class._file_matches)
        namespace = {
            'Paths': SimpleNamespace(App=SimpleNamespace(Backups=self.backup.parent, copy_file=copy_file)),
            'Config': SimpleNamespace(Manager=manager),
        }
        tree = ast.parse(APPLICATION.read_text(encoding='utf-8'))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'load_config')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(APPLICATION), 'exec'), namespace)
        self.load_config = lambda: namespace['load_config'](SimpleNamespace())

    def test_backup_is_written_once_then_skipped_while_identical(self):
        self.config.write_bytes(b'{"a": 1}\r\n')
        self.load_config()
        self.load_config()
        self.assertEqual(self.copies, [self.backup])
        self.assertEqual(self.backup.read_bytes(), self.config.read_bytes())

    def test_changed_configuration_refreshes_the_backup(self):
        self.config.write_bytes(b'{"a": 1}')
        self.load_config()
        self.config.write_bytes(b'{"a": 2}')
        self.load_config()
        self.assertEqual(len(self.copies), 2)
        self.assertEqual(self.backup.read_bytes(), b'{"a": 2}')


if __name__ == '__main__':
    unittest.main()
