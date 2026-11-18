import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools' / 'perf' / 'gui_startup_ab.py'
spec = importlib.util.spec_from_file_location('gui_startup_ab', SOURCE)
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)

REQUIREMENTS = (ROOT / 'requirements.txt').read_text(encoding='utf-8')


class RequirementFilterTests(unittest.TestCase):
    def names(self, lines):
        return {line.split('==')[0].split('@')[0].strip().lower() for line in lines}

    def test_windows_keeps_every_runtime_pin_and_drops_the_build_tool(self):
        lines = ab.requirement_lines(REQUIREMENTS, windows=True)
        names = self.names(lines)
        self.assertNotIn('nuitka', names)
        self.assertTrue({'pywin32', 'wmi', 'winshell', 'pillow', 'certifi', 'av'} <= names)
        self.assertTrue(any(line.startswith('av @') for line in lines))  # the pinned Windows wheel

    def test_other_platforms_drop_windows_packages_and_install_pyav_by_version(self):
        lines = ab.requirement_lines(REQUIREMENTS, windows=False)
        names = self.names(lines)
        self.assertFalse({'nuitka', 'pywin32', 'wmi', 'winshell'} & names)
        self.assertIn('av==19.0.1', lines)
        self.assertFalse(any('@' in line for line in lines))

    def test_each_pin_is_kept_exactly(self):
        pins = [line.strip() for line in REQUIREMENTS.splitlines() if line.lower().startswith(('pillow', 'certifi'))]
        for windows in (True, False):
            with self.subTest(windows=windows):
                lines = ab.requirement_lines(REQUIREMENTS, windows)
                for pin in pins:
                    self.assertIn(pin, lines)

    def test_interpreter_path_per_platform(self):
        self.assertEqual(ab.venv_python(Path('v'), True), Path('v/Scripts/python.exe'))
        self.assertEqual(ab.venv_python(Path('v'), False), Path('v/bin/python'))


class SideWiringTests(unittest.TestCase):
    def test_each_side_starts_with_its_own_environment_sources_and_assets(self):
        made, started = [], []

        def fake_make_env(tree, venv, windows):
            made.append((tree, venv.name))
            return Path(f'/envs/{venv.name}/python')

        def fake_startup(python, tree, runs, launches):
            started.append((str(python), tree, runs, launches))
            return {'tree': str(tree)}

        with tempfile.TemporaryDirectory() as work:
            base, head = Path(work) / 'base', Path(work) / 'head'
            for tree, pin in ((base, 'old'), (head, 'new')):
                tree.mkdir()
                (tree / 'requirements.txt').write_text(f'pillow=={pin}\n', encoding='utf-8')
            with mock.patch.object(ab, 'make_env', fake_make_env), \
                    mock.patch.object(ab, 'startup', fake_startup), \
                    mock.patch.object(ab, 'installed_versions',
                                      lambda python: {'pillow': 'old' if 'base' in str(python) else 'new'}), \
                    mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(Path(work) / 'summary.md')}), \
                    mock.patch('builtins.print'):
                status = ab.main(['--base', str(base), '--head', str(head), '--work', work,
                                  '--runs', '3', '--launches', '2'])
            summary = (Path(work) / 'summary.md').read_text(encoding='utf-8')
            base, head = base.resolve(), head.resolve()
        self.assertEqual(status, 0)
        self.assertEqual(made, [(base, 'venv-base'), (head, 'venv-head')])
        self.assertEqual(started, [('/envs/venv-base/python', base, 3, 2),
                                   ('/envs/venv-head/python', head, 3, 2)])
        self.assertIn('- base (pillow old)', summary)
        self.assertIn('- this change (pillow new)', summary)

    def test_a_side_running_the_other_sides_versions_fails_the_run(self):
        with tempfile.TemporaryDirectory() as work:
            for name in ('base', 'head'):
                (Path(work) / name).mkdir()
                (Path(work) / name / 'requirements.txt').write_text(f'pillow=={name}\n', encoding='utf-8')
            with mock.patch.object(ab, 'make_env', lambda tree, venv, windows: Path('/py')), \
                    mock.patch.object(ab, 'startup', lambda *a: {}), \
                    mock.patch.object(ab, 'installed_versions', lambda python: {'pillow': 'head'}), \
                    mock.patch('builtins.print'):
                with self.assertRaises(RuntimeError):
                    ab.main(['--base', str(Path(work) / 'base'), '--head', str(Path(work) / 'head'), '--work', work])

    def test_windows_runners_build_windows_environments(self):
        seen = []
        with tempfile.TemporaryDirectory() as work:
            for name in ('base', 'head'):
                (Path(work) / name).mkdir()
                (Path(work) / name / 'requirements.txt').write_text('', encoding='utf-8')
            with mock.patch.object(ab.sys, 'platform', 'win32'), \
                    mock.patch.object(ab, 'make_env', lambda tree, venv, windows: (seen.append(windows), Path('/py'))[1]), \
                    mock.patch.object(ab, 'startup', lambda *a: {}), \
                    mock.patch.object(ab, 'installed_versions', lambda python: {}), \
                    mock.patch('builtins.print'), mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop('GITHUB_STEP_SUMMARY', None)
                ab.main(['--base', str(Path(work) / 'base'), '--head', str(Path(work) / 'head'), '--work', work])
        self.assertEqual(seen, [True, True])

    def test_a_failing_start_up_benchmark_fails_the_run(self):
        failed = mock.Mock(returncode=1, stdout='', stderr='boom')
        with mock.patch.object(ab.subprocess, 'run', return_value=failed):
            with self.assertRaises(RuntimeError):
                ab.startup(Path('/py'), Path('/tree'), 1, 1)

    def test_start_up_uses_the_given_interpreter_sources_and_assets(self):
        done = mock.Mock(returncode=0, stdout='noise\n{"ok": 1}\n', stderr='')
        with mock.patch.object(ab.subprocess, 'run', return_value=done) as run:
            self.assertEqual(ab.startup(Path('/venv/bin/python'), Path('/tree'), 4, 2), {'ok': 1})
        command = run.call_args.args[0]
        self.assertIn('/venv/bin/python', command)
        self.assertEqual(command[command.index('--runs') + 1], '4')
        self.assertEqual(command[command.index('--launches') + 1], '2')
        self.assertEqual(command[command.index('--src') + 1], str(Path('/tree/src/xxmi_launcher')))
        self.assertEqual(command[command.index('--assets') + 1], str(Path('/tree')))


class EnvironmentTests(unittest.TestCase):
    def test_each_tree_gets_a_venv_with_its_own_pins(self):
        calls = []
        with tempfile.TemporaryDirectory() as work, \
                mock.patch.object(ab.subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs))):
            results = {}
            for name, pin in (('base', '12.3.0'), ('head', '13.0.0')):
                tree = Path(work) / name
                tree.mkdir()
                (tree / 'requirements.txt').write_text(f'Nuitka==1.0\npillow=={pin}\npywin32==312', encoding='utf-8')
                venv = Path(work) / f'venv-{name}'
                venv.mkdir()
                results[name] = (ab.make_env(tree, venv, windows=False), venv)
            for name, pin in (('base', '12.3.0'), ('head', '13.0.0')):
                python, venv = results[name]
                with self.subTest(side=name):
                    self.assertEqual(python, ab.venv_python(venv, False))
                    self.assertEqual((venv / 'requirements.txt').read_text(encoding='utf-8'), f'pillow=={pin}\n')
                    install = next(c for c in calls if str(venv / 'requirements.txt') in c[0])
                    self.assertEqual(install[0][:5], [str(python), '-m', 'pip', 'install', '--disable-pip-version-check'])
                    self.assertEqual(install[0][-2:], ['-r', str(venv / 'requirements.txt')])
                    self.assertTrue(install[1].get('check'))
            creates = [c for c in calls if c[0][1:3] == ['-m', 'venv']]
            self.assertEqual([c[0][3] for c in creates], [str(results['base'][1]), str(results['head'][1])])
            self.assertTrue(all(c[1].get('check') for c in creates))

    def test_pins_are_read_from_requirements(self):
        text = 'Pillow==13.0.0\ncertifi == 2026.10.23\nrequests==2.34.2\nav @ https://x/av-19.0.1-cp312.whl'
        self.assertEqual(ab.pinned_versions(text), {'pillow': '13.0.0', 'certifi': '2026.10.23'})


if __name__ == '__main__':
    unittest.main()
