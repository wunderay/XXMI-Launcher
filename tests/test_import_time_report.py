import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'ci' / 'import_time_report.py'
spec = importlib.util.spec_from_file_location('import_time_report', SOURCE)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class PlatformExclusionTests(unittest.TestCase):
    def test_only_known_windows_dependencies_are_excluded(self):
        self.assertTrue(report.is_platform_exclusion("ModuleNotFoundError: No module named 'win32api'"))
        self.assertTrue(report.is_platform_exclusion("ModuleNotFoundError: No module named 'winreg'"))
        self.assertTrue(report.is_platform_exclusion("AttributeError: module 'ctypes' has no attribute 'WinDLL'"))

    def test_other_import_errors_are_failures(self):
        self.assertFalse(report.is_platform_exclusion("ModuleNotFoundError: No module named 'requests'"))
        self.assertFalse(report.is_platform_exclusion("ModuleNotFoundError: No module named 'core'"))
        self.assertFalse(report.is_platform_exclusion("AttributeError: module 'ctypes' has no attribute 'cdll'"))
        self.assertFalse(report.is_platform_exclusion('SyntaxError: invalid syntax'))
        self.assertFalse(report.is_platform_exclusion(None))


class DiscoveryAndTimingTests(unittest.TestCase):
    def test_discovers_dotted_core_module_names(self):
        modules = report.discover()
        self.assertIn('core', modules)
        self.assertIn('core.mod_manager', modules)
        self.assertFalse(any(name.endswith('__init__') for name in modules))

    def test_reads_the_cumulative_time_of_the_imported_module(self):
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(report.SOURCE), os.environ.get('PYTHONPATH')])))
        value, error, _ = report.run_once('json', env)
        self.assertIsNone(error)
        self.assertIsInstance(value, int)
        self.assertGreater(value, 0)
        value, error, _ = report.run_once('module_that_does_not_exist', env)
        self.assertIsNone(value)
        self.assertIn('module_that_does_not_exist', error)


class ExitStatusTests(unittest.TestCase):
    def run_main(self, outcomes):
        def fake_run_once(module, env):
            value, error = outcomes[module]
            return value, error, f'import time: {value} | {value} | {module}' if value else ''

        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(report, 'discover', return_value=list(outcomes)), \
                mock.patch.object(report, 'run_once', fake_run_once), \
                mock.patch.object(sys, 'argv', ['import_time_report.py', '--output-dir', out, '--runs', '2']), \
                mock.patch.dict(os.environ, {}, clear=False), mock.patch('builtins.print'):
            os.environ.pop('GITHUB_STEP_SUMMARY', None)
            status = report.main()
            return status, json.loads((Path(out) / 'import-times.json').read_text(encoding='utf-8'))

    def test_an_unexpected_import_error_fails_the_run(self):
        status, data = self.run_main({
            'core.good': (1500, None),
            'core.windows': (None, "ModuleNotFoundError: No module named 'win32api'"),
            'core.broken': (None, 'RuntimeError: portable module regression'),
        })
        self.assertEqual(status, 1)
        self.assertEqual([item['module'] for item in data['failed']], ['core.broken'])
        self.assertEqual([item['module'] for item in data['skipped']], ['core.windows'])
        self.assertEqual([item['module'] for item in data['measured']], ['core.good'])

    def test_a_module_that_never_reports_a_time_fails_the_run(self):
        # The warm-up import succeeds, but no measured run yields an importtime line
        def fake_run_once(module, env):
            return None, None, ''

        with tempfile.TemporaryDirectory() as out, \
                mock.patch.object(report, 'discover', return_value=['core.silent']), \
                mock.patch.object(report, 'run_once', fake_run_once), \
                mock.patch.object(sys, 'argv', ['import_time_report.py', '--output-dir', out, '--runs', '2']), \
                mock.patch.dict(os.environ, {}, clear=False), mock.patch('builtins.print'):
            os.environ.pop('GITHUB_STEP_SUMMARY', None)
            self.assertEqual(report.main(), 1)
            data = json.loads((Path(out) / 'import-times.json').read_text(encoding='utf-8'))
        self.assertEqual([item['module'] for item in data['failed']], ['core.silent'])
        self.assertEqual(data['skipped'], [])

    def test_only_platform_exclusions_pass(self):
        status, data = self.run_main({
            'core.good': (1500, None),
            'core.windows': (None, "ModuleNotFoundError: No module named 'winreg'"),
        })
        self.assertEqual(status, 0)
        self.assertEqual(data['failed'], [])


if __name__ == '__main__':
    unittest.main()
