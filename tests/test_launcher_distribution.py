'''Cross-platform installation layout checks; Windows runtime smoke remains required.'''
import argparse
import ast
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools/PreparedLoading' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packager = load_helper('launcher_packager', 'package-launcher.py')
bootstrap = load_helper('launcher_bootstrap', 'launcher-bootstrap.py')


class LauncherDistributionTests(unittest.TestCase):
    def test_staged_payload_matches_original_frozen_root_and_cleans_stale_files(self):
        with tempfile.TemporaryDirectory(prefix='launcher layout ') as temporary:
            workspace = Path(temporary)
            source, output = workspace / 'source', workspace / 'output'
            frozen = output / 'build/dist/XXMI Launcher'
            (frozen / '_internal').mkdir(parents=True)
            executable = b'fixture executable; never executed'
            (frozen / 'XXMI Launcher.exe').write_bytes(executable)
            (frozen / '_internal/runtime.dll').write_bytes(b'fixture runtime')
            for directory in ('Themes', 'Locale'):
                (source / directory).mkdir(parents=True)
                (source / directory / 'asset.json').write_text('{}')
                (source / directory / '__pycache__').mkdir()
                (source / directory / '__pycache__/stale.pyc').write_bytes(b'stale')
            (source / 'Locale/active_locale.cfg').write_text('developer selection')
            (source / 'LICENSE').write_text('fixture license')
            (output / 'install-payload').mkdir()
            (output / 'install-payload/stale.dll').write_bytes(b'old payload')

            payload = packager.stage_payload(source, output)
            installed_executable = payload / 'Resources/Bin/XXMI Launcher.exe'
            self.assertEqual(installed_executable.read_bytes(), executable)
            self.assertTrue((payload / 'Resources/Bin/_internal/runtime.dll').is_file())
            self.assertFalse((payload / 'XXMI Launcher.exe').exists())
            self.assertFalse((payload / 'stale.dll').exists())
            self.assertFalse((payload / 'Locale/active_locale.cfg').exists())
            self.assertFalse(list(payload.rglob('__pycache__')))
            hashes = packager.file_hashes(payload)
            self.assertEqual(len(hashes), 5)
            self.assertEqual(hashes['Resources/Bin/XXMI Launcher.exe'],
                             hashlib.sha256(executable).hexdigest())

            # Execute only the original application's root-selection branch,
            # without importing platform dependencies or starting Application.
            tree = ast.parse((ROOT / 'src/xxmi_launcher/app.py').read_text())
            entry = next(node for node in tree.body if isinstance(node, ast.If)
                         and ast.unparse(node.test) == "__name__ == '__main__'")
            selection = next(node for node in entry.body if isinstance(node, ast.If))
            namespace = {'Path': Path, 'sys': SimpleNamespace(executable=str(installed_executable), frozen=True),
                         '__launcher_root__': workspace / 'obsolete override'}
            exec(compile(ast.Module(body=[selection], type_ignores=[]), 'app-root', 'exec'), namespace)
            self.assertEqual(namespace['root_path'], payload)
            self.assertTrue((namespace['root_path'] / 'Themes/asset.json').is_file())
            self.assertTrue((namespace['root_path'] / 'Locale/asset.json').is_file())

    def test_bootstrap_uses_installation_root_for_self_test_and_normal_dispatch(self):
        with tempfile.TemporaryDirectory(prefix='launcher bootstrap ') as temporary:
            root = Path(temporary) / 'XXMI Launcher'
            executable = root / 'Resources/Bin/XXMI Launcher.exe'
            with patch.object(bootstrap.sys, 'executable', str(executable)), \
                    patch.object(bootstrap.sys, 'argv', [str(executable), '--self-test']), \
                    patch.dict(bootstrap.os.environ, {}, clear=True), \
                    patch.object(bootstrap.multiprocessing, 'freeze_support'), \
                    patch.object(bootstrap, 'self_test') as self_test:
                bootstrap.main()
                self_test.assert_called_once_with(root)
            with patch.object(bootstrap.sys, 'executable', str(executable)), \
                    patch.object(bootstrap.sys, 'argv', [str(executable)]), \
                    patch.object(bootstrap.multiprocessing, 'freeze_support'), \
                    patch.object(bootstrap.runpy, 'run_module') as run_module:
                bootstrap.main()
                run_module.assert_called_once_with('app', run_name='__main__')

    def test_version_resource_round_trip_and_mismatch_detection(self):
        for version, expected in (('0.0.0', (0, 0, 0, 0)),
                                  ('1.27.300', (1, 27, 300, 0)),
                                  ('65535.65535.65535', (65535, 65535, 65535, 0))):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                resource = Path(temporary) / 'launcher-version.txt'
                with patch.object(packager.os.sys, 'argv',
                                  ['package-launcher.py', '--version', version,
                                   '--version-resource', str(resource)]):
                    packager.main()
                tree = ast.parse(resource.read_text())
                fixed = next(node for node in ast.walk(tree)
                             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id == 'FixedFileInfo')
                fields = {item.arg: ast.literal_eval(item.value) for item in fixed.keywords}
                self.assertEqual(fields['filevers'], expected)
                self.assertEqual(fields['prodvers'], expected)
                text_versions = [ast.literal_eval(node.args[1]) for node in ast.walk(tree)
                                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                 and node.func.id == 'StringStruct'
                                 and ast.literal_eval(node.args[0]) in ('FileVersion', 'ProductVersion')]
                self.assertEqual(text_versions, [version + '.0', version + '.0'])
                info = {'FileVersionMS': expected[0] * 65536 + expected[1],
                        'FileVersionLS': expected[2] * 65536 + expected[3]}
                bootstrap.verify_executable_version(version, info)
                with self.assertRaisesRegex(RuntimeError, 'differs from launcher manifest'):
                    bootstrap.verify_executable_version(version, dict(info, FileVersionLS=info['FileVersionLS'] + 1))

    def test_invalid_versions_are_rejected_before_generating_resources(self):
        invalid = ('', '1', '1.2', '1.2.3.4', '-1.2.3', '1.2.-3', '1.2.65536',
                   '65536.0.0', '1.2.03', '1.2.3-dev', '1.2.3\n', '１.2.3', '1.2.$(command)')
        for version in invalid:
            with self.subTest(version=version):
                with self.assertRaises(argparse.ArgumentTypeError):
                    packager.version_resource(version)
                with self.assertRaises(RuntimeError):
                    bootstrap.verify_executable_version(version, {'FileVersionMS': 0, 'FileVersionLS': 0})

    def test_missing_compiled_executable_preserves_previous_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'install-payload').mkdir()
            marker = output / 'install-payload/previous-payload'
            marker.write_bytes(b'previous')
            with self.assertRaisesRegex(RuntimeError, 'Missing frozen Windows executable'):
                packager.stage_payload(output / 'source', output)
            self.assertEqual(marker.read_bytes(), b'previous')


if __name__ == '__main__':
    unittest.main()
