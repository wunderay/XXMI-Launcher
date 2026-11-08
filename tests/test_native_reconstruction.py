"""Portable reconstruction must reject changed inputs and preserve provenance."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'tools/PreparedLoading/reconstruct-native.py'
spec = importlib.util.spec_from_file_location('native_reconstruction', SCRIPT)
reconstruction = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconstruction)


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xxmi-source-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.native = self.root / 'upstream'
        self.native.mkdir()
        self.launcher = self.root / 'launcher'
        self.tools = self.launcher / reconstruction.TOOLS
        self.tools.mkdir(parents=True)
        self.inputs = self.launcher / reconstruction.CAMPAIGN / 'source-inputs.json'
        self.inputs.parent.mkdir(parents=True)
        self.git('init', '-q')
        (self.native / 'native.cpp').write_bytes(b'base\n')
        self.commit()
        base = self.git('rev-parse', 'HEAD').decode().strip()
        (self.native / 'native.cpp').write_bytes(b'parent\n')
        self.commit()
        parent = self.git('rev-parse', 'HEAD').decode().strip()
        self.candidate = {'native_base': base, 'native_parent': parent,
                          'native_tree': self.git('rev-parse', 'HEAD^{tree}').decode().strip()}
        self.write('native.patch', self.git('diff', '--binary', base, parent))
        for index, name in enumerate(reconstruction.PATCHES[1:]):
            (self.native / 'native.cpp').write_bytes(('step%d\n' % index).encode())
            patch = self.git('diff', '--binary', 'HEAD')
            # Reproduce the real retention input's CRLF transport without
            # changing any hunk's content or its pinned original bytes.
            if name == 'native-gpu-retention.patch':
                patch = patch.replace(b'\n', b'\r\n')
            self.write(name, patch)
            self.commit()
        bundle = self.tools / 'integration-evidence/native-history.bundle'
        bundle.parent.mkdir()
        self.git('branch', 'pinned-parent', parent)
        self.git('bundle', 'create', str(bundle), 'pinned-parent', '^' + base)
        self.write('baseline/version.generated.h', b'#define VERSION 1\r\n')
        self.candidate['patch_sha256'] = reconstruction.digest((self.tools / 'native.patch').read_bytes())
        self.write('native-candidate.json', json.dumps(self.candidate).encode())
        files = {'native.cpp': b'step3\n', 'version.generated.h': b'#define VERSION 1\r\n'}
        hasher = hashlib.sha256()
        for name, content in sorted(files.items()):
            hasher.update((name + '\0' + hashlib.sha256(content).hexdigest() + '\n').encode())
        self.expected_digest = hasher.hexdigest()
        self.historical = self.launcher / 'development-builds/prepared-loading/BUILD-INFO.json'
        self.historical.parent.mkdir(parents=True)
        self.historical.write_text(json.dumps({'native_source_digest': self.expected_digest}))
        self.pin_inputs()
        # An existing dirty source checkout must not contaminate reconstruction.
        (self.native / 'native.cpp').write_bytes(b'uncommitted unrelated work\n')

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.autocrlf=false',
            '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=' + str(self.root / 'no-hooks'),
            '-c', 'user.name=XXMI fixture', '-c', 'user.email=fixture@example.invalid',
            '-C', str(self.native), *args], stderr=subprocess.PIPE)

    def commit(self):
        self.git('add', 'native.cpp')
        self.git('commit', '-q', '-m', 'fixture source')

    def write(self, relative, content):
        path = self.tools / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def pin_inputs(self):
        paths = sorted(self.tools.rglob('*')) + [self.historical]
        entries = [{'path': p.relative_to(self.launcher).as_posix(),
                    'bytes': p.stat().st_size, 'sha256': reconstruction.digest(p.read_bytes())}
                   for p in paths if p.is_file()]
        self.inputs.write_text(json.dumps({'schema': 'xxmi.campaign-source-inputs.v1', 'files': entries}))

    def reconstruct(self, name='candidate', **kwargs):
        return reconstruction.reconstruct(self.launcher, self.native, self.root / name, self.inputs, **kwargs)

    def test_aggregate_and_bundle_reconstruct_identical_source_with_exact_header(self):
        original = (self.tools / 'native-gpu-retention.patch').read_bytes()
        aggregate = self.reconstruct(verify_bundle=True)
        bundle = self.reconstruct('bundled', route='bundle')
        self.assertEqual(aggregate['source_digest'], self.expected_digest)
        self.assertEqual(aggregate['source_digest'], bundle['source_digest'])
        self.assertEqual(aggregate['reconstructed_git_tree'], bundle['reconstructed_git_tree'])
        self.assertIsNone(aggregate['historical_source_matches'])
        self.assertTrue(aggregate['bundle_verified'])
        self.assertEqual(aggregate['patch_application'][-1]['application_transform'], 'crlf-to-lf')
        self.assertEqual((self.root / 'candidate/version.generated.h').read_bytes(), b'#define VERSION 1\r\n')
        self.assertEqual((self.tools / 'native-gpu-retention.patch').read_bytes(), original)
        self.assertEqual((self.native / 'native.cpp').read_bytes(), b'uncommitted unrelated work\n')

    def test_changed_patch_fails_before_destination_creation(self):
        with (self.tools / 'native-speed.patch').open('ab') as stream:
            stream.write(b'\n')
        with self.assertRaisesRegex(reconstruction.ReconstructionError, 'input identity mismatch'):
            self.reconstruct()
        self.assertFalse((self.root / 'candidate').exists())

    def test_manifest_rejects_duplicate_and_escaping_paths(self):
        value = json.loads(self.inputs.read_text())
        original = self.inputs.read_text()
        value['files'].append(value['files'][0])
        self.inputs.write_text(json.dumps(value))
        with self.assertRaisesRegex(reconstruction.ReconstructionError, 'duplicate input path'):
            self.reconstruct()
        value = json.loads(original)
        value['files'][0]['path'] = '../outside'
        self.inputs.write_text(json.dumps(value))
        with self.assertRaisesRegex(reconstruction.ReconstructionError, 'relative child'):
            self.reconstruct()

    def test_wrong_pinned_parent_tree_is_rejected(self):
        self.candidate['native_tree'] = '0' * 40
        self.write('native-candidate.json', json.dumps(self.candidate).encode())
        self.pin_inputs()
        with self.assertRaisesRegex(reconstruction.ReconstructionError, 'differs from pinned parent tree'):
            self.reconstruct()

    def test_out_of_order_patch_chain_is_not_repaired_or_fuzzed(self):
        first, second = self.tools / reconstruction.PATCHES[1], self.tools / reconstruction.PATCHES[2]
        first_bytes, second_bytes = first.read_bytes(), second.read_bytes()
        first.write_bytes(second_bytes)
        second.write_bytes(first_bytes)
        self.pin_inputs()
        with self.assertRaisesRegex(reconstruction.ReconstructionError, 'git apply failed'):
            self.reconstruct()

    def test_missing_version_header_is_rejected_before_copy(self):
        (self.tools / 'baseline/version.generated.h').unlink()
        with self.assertRaisesRegex(reconstruction.ReconstructionError, 'missing or escapes'):
            self.reconstruct()
        self.assertFalse((self.root / 'candidate').exists())

    def test_existing_destination_is_never_overwritten(self):
        destination = self.root / 'candidate'
        destination.mkdir()
        marker = destination / 'preserve'
        marker.write_bytes(b'keep')
        with self.assertRaisesRegex(reconstruction.ReconstructionError, 'must not exist'):
            self.reconstruct()
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_helper_edits_are_reported_separately_from_native_inputs(self):
        self.write('helper.py', b'original helper\n')
        self.pin_inputs()
        self.write('helper.py', b'changed helper\n')
        report = self.reconstruct()
        helper = next(entry for entry in report['inputs'] if entry['path'].endswith('/helper.py'))
        self.assertFalse(helper['matches_pin'])
        self.assertEqual(helper['role'], 'helper-provenance')
        self.assertEqual(report['source_digest'], self.expected_digest)

    def test_historical_archive_name_scope_is_not_confused_with_full_source(self):
        import io
        from zipfile import ZipFile
        inner = io.BytesIO()
        with ZipFile(inner, 'w') as source:
            source.writestr('native/native.cpp', b'old archived bytes')
        archive = self.historical.with_name('XXMI-Prepared-Loading-development-win-x64.zip')
        with ZipFile(archive, 'w') as outer:
            outer.writestr('corresponding-source.zip', inner.getvalue())
        report = self.reconstruct()
        scope = report['historical_archive_scope']
        self.assertEqual(scope['files'], 1)
        self.assertEqual(scope['missing'], [])
        self.assertNotEqual(scope['relative_name_scope_digest'], report['source_digest'])
        self.assertFalse(scope['relative_name_scope_matches_record'])
        self.assertFalse(report['historical_source_matches'])

    def test_historical_match_uses_original_archive_name_scope(self):
        import io
        from zipfile import ZipFile
        inner = io.BytesIO()
        with ZipFile(inner, 'w') as source:
            source.writestr('native/native.cpp', b'old archived bytes')
        archive = self.historical.with_name('XXMI-Prepared-Loading-development-win-x64.zip')
        with ZipFile(archive, 'w') as outer:
            outer.writestr('corresponding-source.zip', inner.getvalue())
        scoped = hashlib.sha256(('native/native.cpp\0' + hashlib.sha256(b'step3\n').hexdigest() + '\n').encode()).hexdigest()
        self.historical.write_text(json.dumps({'native_source_digest': scoped}))
        self.pin_inputs()
        report = self.reconstruct()
        self.assertNotEqual(report['source_digest'], report['historical_source_digest'])
        self.assertEqual(report['source_digest'], self.expected_digest)
        self.assertTrue(report['historical_source_matches'])
        self.assertEqual(report['historical_archive_scope']['zip_name_scope_digest'], scoped)

    def test_cli_historical_mismatch_emits_receipt_and_fails(self):
        self.historical.write_text(json.dumps({'native_source_digest': 'f' * 64}))
        self.pin_inputs()
        receipt = self.root / 'receipt.json'
        result = subprocess.run([sys.executable, str(SCRIPT), '--launcher', str(self.launcher),
            '--native', str(self.native), '--output', str(self.root / 'candidate'),
            '--receipt', str(receipt)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'NOT PROVEN', result.stderr)
        report = json.loads(receipt.read_text())
        self.assertEqual(report['source_digest'], self.expected_digest)
        self.assertIsNone(report['historical_source_matches'])


if __name__ == '__main__':
    unittest.main()
