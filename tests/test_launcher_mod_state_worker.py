"""Opt-in Windows smoke of the real, built, copied frozen snapshot worker.

XXMI_MOD_STATE_TEST_BUNDLE must name an explicit install-payload directory.
All game/config/mod data and all restore targets are temporary inert fixtures.
This test never opens an installed launcher or launches a game.
"""
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import time
import unittest


BUNDLE = os.environ.get('XXMI_MOD_STATE_TEST_BUNDLE')
REPOSITORY = Path(__file__).resolve().parents[1]

# Only metadata used to select the production frozen runtime is patched. The
# preparer, actual built EXE, copied runtime, parent handle and engine are real.
PARENT_HARNESS = r'''
import ctypes
import json
from pathlib import Path
import sys
from unittest.mock import patch
from core import mod_state_worker as worker

operation, root, archive, executable = sys.argv[1:]
executable = Path(executable)
# Match the inherited Windows DLL search directory of a real frozen parent,
# in addition to its Python metadata. The copied bootloader must use its own DLLs.
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.SetDllDirectoryW.argtypes = (ctypes.c_wchar_p,)
kernel.SetDllDirectoryW.restype = ctypes.c_int
if not kernel.SetDllDirectoryW(str(executable.parent / '_internal')):
    raise ctypes.WinError(ctypes.get_last_error())
try:
    with patch.object(sys, 'frozen', True, create=True), \
            patch.object(sys, 'executable', str(executable)), \
            patch.object(sys, '_MEIPASS', str(executable.parent / '_internal'), create=True):
        job = worker.prepare_snapshot_job(operation, Path(root), Path(archive), notify=False)
finally:
    kernel.SetDllDirectoryW(None)
print(json.dumps({
    'result_path': str(job['result_path']),
    'job_dir': str(job['job_dir']),
    'worker_pid': job['process'].pid,
    'worker_creation': worker.process_identity(job['process'].pid),
}), flush=True)
sys.stdin.readline()
'''


@unittest.skipUnless(sys.platform == 'win32' and bool(BUNDLE),
                     'Requires Windows and explicit XXMI_MOD_STATE_TEST_BUNDLE build')
class PackagedModStateWorkerTests(unittest.TestCase):
    def remaining(self, ceiling=40):
        available = self.deadline - time.monotonic()
        self.assertGreater(available, 0, 'Packaged snapshot smoke exceeded its bounded deadline')
        return min(ceiling, available)

    @staticmethod
    def tree_bytes(root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob('*') if path.is_file()}

    @staticmethod
    def digest(path):
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                checksum.update(chunk)
        return checksum.hexdigest()

    def run_operation(self, operation, root, archive, executable):
        import psutil
        from core import mod_state_worker as worker

        environment = dict(os.environ, PYTHONPATH=str(REPOSITORY / 'src/xxmi_launcher'),
                           PYTHONIOENCODING='utf-8')
        parent = subprocess.Popen(
            [sys.executable, '-u', '-c', PARENT_HARNESS, operation, str(root),
             str(archive), str(executable)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env=environment, cwd=str(root.parent),
        )
        # Popen tracks only our exact initiating harness; the helper gets its own
        # verified birth identity below. No process-name-based termination occurs.
        helper = None
        helper_creation = None
        job_dir = None
        output = Queue()
        reader = Thread(target=lambda: output.put(parent.stdout.readline()), daemon=True)
        reader.start()
        try:
            try:
                line = output.get(timeout=self.remaining())
            except Empty:
                self.fail('Built snapshot helper did not report readiness within the bounded wait')
            if not line:
                _, error = parent.communicate('\n', timeout=self.remaining(5))
                self.fail(f'Initiating harness exited before readiness ({parent.returncode}): {error}')
            job = json.loads(line)
            job_dir = Path(job['job_dir'])
            report = Path(job['result_path'])
            self.assertNotEqual(job_dir, root)
            self.assertFalse(job_dir.is_relative_to(root))
            helper = psutil.Process(job['worker_pid'])
            helper_creation = job['worker_creation']
            self.assertEqual(worker.process_identity(helper.pid), helper_creation)
            expected_executable = job_dir / 'runtime' / executable.name
            self.assertEqual(Path(helper.exe()).resolve(), expected_executable.resolve())
            self.assertEqual(self.digest(expected_executable), self.digest(executable))
            self.assertTrue((job_dir / 'runtime' / '_internal' / 'python312.dll').is_file(),
                            'Smoke must execute a complete real frozen runtime')
            request = json.loads((job_dir / 'request.json').read_text(encoding='utf-8'))
            self.assertIs(request['frozen'], True)
            self.assertIs(request['notify'], False)

            before = self.tree_bytes(root)
            # Readiness includes an exact initiating-process handle. Keep that
            # parent alive long enough to catch a worker that incorrectly skips it.
            time.sleep(min(0.25, self.remaining(0.25)))
            self.assertIsNone(parent.poll())
            self.assertTrue(helper.is_running())
            self.assertFalse(report.exists(), 'Worker acted before initiating parent exit')
            self.assertEqual(self.tree_bytes(root), before)
            if operation == 'backup':
                self.assertFalse(archive.exists(), 'Backup started before initiating parent exit')
            self.assertFalse(list(root.parent.glob(root.name + '.rollback-*')),
                             'Restore started before initiating parent exit')

            _, error = parent.communicate('\n', timeout=self.remaining(5))
            self.assertEqual(parent.returncode, 0, error)
            # A result file is written before final process exit; wait for the
            # exact helper too before touching its Windows mapped runtime files.
            exit_code = helper.wait(timeout=self.remaining(30))
            self.assertTrue(report.is_file(), 'Built worker exited without a completion report')
            result = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(exit_code, 0, result)
            self.assertEqual(result['status'], 'complete', result)
            self.assertEqual(result['operation'], operation)
            return result
        finally:
            if parent.poll() is None:
                try:
                    parent.communicate('\n', timeout=5)
                except subprocess.TimeoutExpired:
                    # This Popen is exclusively our test-created harness.
                    parent.kill()
                    parent.communicate(timeout=5)
            helper_stopped = helper is not None and not helper.is_running()
            if helper is not None and not helper_stopped:
                try:
                    helper.wait(timeout=3)
                    helper_stopped = True
                except psutil.TimeoutExpired:
                    # PID reuse must never authorize terminating another process.
                    try:
                        if worker.process_identity(helper.pid) == helper_creation and helper.is_running():
                            helper.kill()
                            helper.wait(timeout=3)
                            helper_stopped = True
                    except (psutil.NoSuchProcess, OSError):
                        helper_stopped = not helper.is_running()
                except psutil.NoSuchProcess:
                    helper_stopped = True
            if job_dir is not None and helper_stopped:
                # Keep small reports for CI evidence, release copied runtime disk space.
                shutil.rmtree(job_dir / 'runtime', ignore_errors=True)
            for pipe in (parent.stdin, parent.stdout, parent.stderr):
                if pipe is not None:
                    pipe.close()

    def test_packaged_copy_waits_for_parent_and_restores_exact_saved_state(self):
        from core import mod_state_snapshot as snapshot

        # One shared deadline bounds both runtime copies and both actual workers;
        # small extra cleanup waits are bounded independently on failure.
        self.deadline = time.monotonic() + 100
        bundle = Path(BUNDLE).resolve(strict=True)
        executable = bundle / 'Resources' / 'Bin' / 'XXMI Launcher.exe'
        self.assertTrue(executable.is_file(), f'Explicit test bundle lacks launcher: {executable}')
        self.assertTrue((executable.parent / '_internal').is_dir())
        with tempfile.TemporaryDirectory(prefix='xxmi-packaged-snapshot-') as temporary:
            parent = Path(temporary)
            root = parent / 'XXMI Launcher fixture'
            zzmi = root / 'ZZMI'
            mods = zzmi / 'Mods'
            (mods / 'Chosen skin').mkdir(parents=True)
            (mods / 'DISABLED old skin').mkdir()
            (root / snapshot.CONFIG).write_text(json.dumps({
                'Importers': {'ZZMI': {'Importer': {'importer_folder': 'ZZMI'}}},
            }), encoding='utf-8')
            (zzmi / 'd3dx.ini').write_text(
                '[Include]\ninclude_recursive = Mods\nexclude_recursive = DISABLED*\n'
                'user_config = d3dx_user.ini\n', encoding='utf-8')
            state = zzmi / 'd3dx_user.ini'
            saved = b'[Constants]\r\n$\\Mods\\Chosen skin\\skin.ini\\dress = 3\r\n'
            state.write_bytes(saved)
            (mods / 'Chosen skin' / 'skin.ini').write_bytes(
                b'[Constants]\r\nglobal persist $dress = 0\r\n')
            (mods / 'Chosen skin' / 'mesh.buf').write_bytes(bytes(range(256)))
            (mods / 'DISABLED old skin' / 'old.ini').write_bytes(b'; keep disabled state\r\n')
            original = self.tree_bytes(root)
            archive = parent / 'exact saved setup.zip'
            self.run_operation('backup', root, archive, executable)
            self.assertTrue(snapshot.verify(archive)['verified'])
            self.assertEqual(self.tree_bytes(root), original)

            state.write_bytes(b'new live settings to retain in rollback')
            stale = mods / 'stale added.ini'
            stale.write_bytes(b'; post-backup file\n')
            live_before_restore = self.tree_bytes(root)
            result = self.run_operation('restore', root, archive, executable)
            self.assertEqual(self.tree_bytes(root), original)
            self.assertEqual(state.read_bytes(), saved)
            self.assertFalse(stale.exists())
            rollback = Path(result['detail']['rollback'])
            self.assertEqual(rollback.parent, root.parent)
            self.assertNotEqual(rollback, root)
            self.assertEqual(self.tree_bytes(rollback), live_before_restore)
            self.assertTrue(snapshot.verify(archive, root)['root_matches'])


if __name__ == '__main__':
    unittest.main()
