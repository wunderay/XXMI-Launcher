import ast
import contextlib
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'src' / 'xxmi_launcher' / 'app.py'
APPLICATION = ROOT / 'src' / 'xxmi_launcher' / 'core' / 'application.py'


def load_function(path: Path, name: str, namespace: dict):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


SESSION = textwrap.dedent('''\
    2026-01-01 000001 core.package_manager DEBUG Loaded package: ZZMI
    2026-01-01 000001 core.event_manager DEBUG FIRED: ApplicationEvents.Launch()
    2026-01-01 000001 core.event_manager DEBUG FIRED: ApplicationEvents.Ready()
''')


class LogRotationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.log = self.root / 'XXMI Launcher Log.txt'
        self.old = self.root / 'XXMI Launcher Log.old.txt'
        namespace = {'os': os, 'Path': Path, 'LOG_ROTATE_BYTES': 8 * 1024 * 1024}
        self.rotate_log = load_function(APP, 'rotate_log', namespace)
        paths = SimpleNamespace(App=SimpleNamespace(Root=self.root))
        app_ns = {'Paths': paths, 'contextlib': contextlib, 'open': open}
        iter_lines = load_function(APPLICATION, '_iter_log_lines', app_ns)
        counters = load_function(APPLICATION, 'get_launch_counters_from_log', app_ns)
        self.count = lambda: counters(SimpleNamespace(_iter_log_lines=iter_lines))

    def test_small_log_is_left_alone(self):
        self.log.write_text(SESSION, encoding='utf-8')
        self.rotate_log(self.log, max_bytes=10_000)
        self.assertTrue(self.log.exists())
        self.assertFalse(self.old.exists())

    def test_large_log_moves_aside_replacing_the_previous_one(self):
        self.old.write_text('older', encoding='utf-8')
        self.log.write_text(SESSION * 10, encoding='utf-8')
        self.rotate_log(self.log, max_bytes=100)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.old.read_text(encoding='utf-8'), SESSION * 10)

    def test_missing_log_does_not_fail(self):
        self.rotate_log(self.log, max_bytes=1)

    def test_launch_counts_survive_a_rotation(self):
        self.log.write_text(SESSION * 5, encoding='utf-8')
        before = self.count()
        self.assertEqual(before['ZZMI'], 5)
        self.rotate_log(self.log, max_bytes=100)
        with open(self.log, 'a', encoding='utf-8') as log:
            log.write(SESSION * 2)
        after = self.count()
        self.assertEqual(after['ZZMI'], 7)


if __name__ == '__main__':
    unittest.main()
