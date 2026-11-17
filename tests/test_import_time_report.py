import importlib.util
import os
import sys
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
