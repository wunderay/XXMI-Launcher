import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from core import mod_state_snapshot as snapshot
from core import mod_state_worker as worker


def windows_short_path(path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetShortPathNameW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    kernel.GetShortPathNameW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetShortPathNameW(str(path), buffer, len(buffer))
    if not size or size >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.root = self.parent / "XXMI Launcher"
        self.zzmi = self.root / "ZZMI"
        (self.zzmi / "Mods" / "DISABLED skin").mkdir(parents=True)
        (self.root / snapshot.CONFIG).write_text(json.dumps({"Importers": {"ZZMI": {
            "Importer": {"importer_folder": "ZZMI"}}}}), encoding="utf-8")
        (self.zzmi / "d3dx.ini").write_text("[Include]\ninclude_recursive = Mods\n", encoding="utf-8")
        self.state = self.zzmi / "d3dx_user.ini"
        self.state.write_bytes(b"[Constants]\r\n$skin = 4\r\n")
        (self.zzmi / "Mods" / "DISABLED skin" / "skin.ini").write_bytes(b"; disabled mod\n")
        self.archive = self.parent / "backup.zip"
        self.job = self.parent / "job"
        self.job.mkdir()

    def request(self, **updates):
        data = {"version": 1, "operation": "backup", "root": str(self.root), "archive": str(self.archive),
                "parent_pid": 999999, "parent_creation": "1234", "wait_timeout": 1,
                "token": "a" * 32, "frozen": False}
        data.update(updates)
        path = self.job / "request.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc identity support")
    def test_parent_wait_checks_birth_identity_before_readiness(self):
        with mock.patch.object(worker, "_proc_identity", return_value=("different", "R")):
            with self.assertRaisesRegex(snapshot.SnapshotError, "identity changed"):
                worker.ParentWaiter(999999, "original")

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc identity support")
    def test_parent_wait_is_bounded_and_never_starts_operation(self):
        with mock.patch.object(worker, "_proc_identity", return_value=("birth", "R")):
            waiter = worker.ParentWaiter(999999, "birth")
            try:
                with self.assertRaisesRegex(snapshot.SnapshotError, "Timed out"):
                    waiter.wait(0.001)
            finally:
                waiter.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc identity support")
    def test_parent_wait_accepts_exit_and_pid_reuse_of_original(self):
        for after in (None, ("replacement", "R"), ("birth", "Z")):
            with self.subTest(after=after):
                with mock.patch.object(worker, "_proc_identity", side_effect=[("birth", "R"), after]):
                    waiter = worker.ParentWaiter(999999, "birth")
                    waiter.wait(0.01)
                    waiter.close()

    def test_windows_parent_handle_pins_creation_time_and_waits_for_exit(self):
        kernel = mock.Mock()
        kernel.OpenProcess.return_value = 123
        kernel.WaitForSingleObject.return_value = 0
        with mock.patch.object(worker.os, "name", "nt"), \
                mock.patch.object(worker, "_kernel", return_value=kernel), \
                mock.patch.object(worker, "_windows_creation", return_value="birth"):
            waiter = worker.ParentWaiter(999999, "birth")
            waiter.wait(0.01)
            waiter.close()
        kernel.OpenProcess.assert_called_once_with(0x100000 | 0x1000, False, 999999)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_windows_parent_handle_identity_mismatch_closes_handle(self):
        kernel = mock.Mock()
        kernel.OpenProcess.return_value = 123
        with mock.patch.object(worker.os, "name", "nt"), \
                mock.patch.object(worker, "_kernel", return_value=kernel), \
                mock.patch.object(worker, "_windows_creation", return_value="replacement"):
            with self.assertRaisesRegex(snapshot.SnapshotError, "PID reuse"):
                worker.ParentWaiter(999999, "birth")
        kernel.CloseHandle.assert_called_once_with(123)

    def test_windows_parent_handle_timeout_and_wait_failure(self):
        for status, error in ((258, "Timed out"), (0xffffffff, "Failed waiting")):
            with self.subTest(status=status):
                kernel = mock.Mock()
                kernel.OpenProcess.return_value = 123
                kernel.WaitForSingleObject.return_value = status
                with mock.patch.object(worker.os, "name", "nt"), \
                        mock.patch.object(worker, "_kernel", return_value=kernel), \
                        mock.patch.object(worker, "_windows_creation", return_value="birth"):
                    waiter = worker.ParentWaiter(999999, "birth")
                    try:
                        with self.assertRaisesRegex(snapshot.SnapshotError, error):
                            waiter.wait(0.001)
                    finally:
                        waiter.close()
                kernel.CloseHandle.assert_called_once_with(123)

    def test_parent_cannot_be_worker_itself(self):
        with self.assertRaisesRegex(snapshot.SnapshotError, "itself"):
            worker.ParentWaiter(os.getpid(), "1")

    def test_worker_wait_failure_does_not_call_engine(self):
        request = self.request()
        with mock.patch.object(worker, "_validate_isolation"), mock.patch.object(worker, "ParentWaiter") as waiter, \
                mock.patch.object(snapshot, "backup") as backup, mock.patch.object(worker, "_notify"):
            waiter.return_value.wait.side_effect = snapshot.SnapshotError("Timed out")
            self.assertEqual(worker.worker_main(str(request)), 1)
            backup.assert_not_called()
        self.assertEqual(json.loads((self.job / "result.json").read_text())["status"], "error")
        self.assertFalse(self.archive.exists())

    def test_worker_isolation_refuses_runtime_or_job_under_root(self):
        with self.assertRaisesRegex(snapshot.SnapshotError, "outside"):
            worker._validate_isolation(self.root / "job", self.root)
        with mock.patch.object(worker, "__file__", str(self.job / "runtime" / "mod_state_worker.py")), \
                mock.patch.object(snapshot, "__file__", str(self.job / "runtime" / "mod_state_snapshot.py")), \
                mock.patch.object(worker.sys, "path", [str(self.root / "original-modules")]):
            with self.assertRaisesRegex(snapshot.SnapshotError, "outside"):
                worker._validate_isolation(self.job, self.root)

    def test_worker_unsafe_request_never_writes_inside_root(self):
        request = self.root / "request.json"
        request.write_text(self.request().read_text(), encoding="utf-8")
        with mock.patch.object(worker, "_notify"):
            self.assertEqual(worker.worker_main(str(request)), 1)
        self.assertFalse((self.root / "result.json").exists())
        self.assertFalse((self.root / "ready.json").exists())

    def test_request_outside_executing_runtime_cannot_delete_unrelated_files(self):
        request = self.request()
        runtime = self.job / "runtime"
        runtime.mkdir()
        marker = runtime / "unrelated.marker"
        marker.write_bytes(b"retain unrelated data")
        report = self.job / "result.json"
        report.write_bytes(b"retain existing report")
        with mock.patch.object(worker, "_notify"):
            self.assertEqual(worker.worker_main(str(request)), 1)
        self.assertEqual(marker.read_bytes(), b"retain unrelated data")
        self.assertEqual(report.read_bytes(), b"retain existing report")
        self.assertFalse((self.job / "ready.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows short/long path aliases")
    def test_job_runtime_ownership_accepts_same_long_and_short_windows_path(self):
        job = self.parent / "Isolated Worker Job With Spaces"
        runtime = job / "runtime"
        runtime.mkdir(parents=True)
        for name in ("mod_state_worker.py", "mod_state_snapshot.py"):
            (runtime / name).write_bytes(b"# owned runtime fixture\n")
        short_job = windows_short_path(job)
        canonical = runtime.resolve()
        if os.path.normcase(str(short_job)) == os.path.normcase(str(job.resolve())):
            self.skipTest("8.3 aliases are disabled on this volume")
        with mock.patch.object(worker, "__file__", str(canonical / "mod_state_worker.py")), \
                mock.patch.object(snapshot, "__file__", str(canonical / "mod_state_snapshot.py")):
            worker._validate_isolation(short_job, self.root)
        with self.assertRaisesRegex(snapshot.SnapshotError, "outside"):
            worker._outside(self.root.resolve() / "backup.zip", windows_short_path(self.root), "archive")

    def test_source_runtime_is_copied_and_uses_isolated_python(self):
        command, frozen = worker._prepare_runtime(self.job, self.root)
        self.assertFalse(frozen)
        self.assertEqual(command[1:3], ["-I", "-S"])
        self.assertTrue((self.job / "runtime" / "mod_state_worker.py").is_file())
        self.assertTrue((self.job / "runtime" / "mod_state_snapshot.py").is_file())
        self.assertFalse(snapshot.inside(Path(command[0]), self.root))

    def test_frozen_runtime_copies_complete_recognized_onedir(self):
        runtime = self.root / "Resources" / "Bin"
        internal = runtime / "_internal"
        internal.mkdir(parents=True)
        executable = runtime / "XXMI Launcher.exe"
        executable.write_bytes(b"runtime executable")
        (internal / "python312.dll").write_bytes(b"runtime dll")
        (runtime / "extra.runtime").write_bytes(b"must also copy")
        with mock.patch.object(worker.sys, "frozen", True, create=True), \
                mock.patch.object(worker.sys, "executable", str(executable)), \
                mock.patch.object(worker.sys, "_MEIPASS", str(internal), create=True):
            command, frozen = worker._prepare_runtime(self.job, self.root)
        self.assertTrue(frozen)
        self.assertEqual(command, [str(self.job / "runtime" / "XXMI Launcher.exe")])
        self.assertEqual((self.job / "runtime" / "extra.runtime").read_bytes(), b"must also copy")
        self.assertEqual((self.job / "runtime" / "_internal" / "python312.dll").read_bytes(), b"runtime dll")

    def test_unrecognized_frozen_layout_refused_without_root_copy(self):
        exe = self.root / "XXMI Launcher.exe"
        exe.write_bytes(b"exe")
        with mock.patch.object(worker.sys, "frozen", True, create=True), \
                mock.patch.object(worker.sys, "executable", str(exe)), \
                mock.patch.object(worker.sys, "_MEIPASS", str(self.root), create=True):
            with self.assertRaisesRegex(snapshot.SnapshotError, "Unsupported frozen"):
                worker._prepare_runtime(self.job, self.root)
        self.assertFalse((self.job / "runtime").exists())

    def test_clean_pyinstaller_environment(self):
        environment = {"_PYI_APPLICATION_HOME_DIR": str(self.root / "Resources" / "Bin" / "_internal"),
                       "_PYI_PARENT_PROCESS_LEVEL": "1", "_MEIPASS2": str(self.root),
                       "PYTHONPATH": str(self.root), "PYTHONHOME": str(self.root),
                       "PATH": os.pathsep.join([str(self.root / "Resources" / "Bin"), "/safe/bin"])}
        with mock.patch.dict(worker.os.environ, environment, clear=True):
            cleaned = worker._child_environment(self.root)
        self.assertEqual(cleaned["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertNotIn("_PYI_PARENT_PROCESS_LEVEL", cleaned)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", cleaned)
        self.assertNotIn("_MEIPASS2", cleaned)
        self.assertNotIn("PYTHONPATH", cleaned)
        self.assertNotIn("PYTHONHOME", cleaned)
        self.assertEqual(cleaned["PATH"], "/safe/bin")

    def test_preflight_failure_does_not_spawn_helper(self):
        with mock.patch.object(worker.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(snapshot.SnapshotError, "outside"):
                worker.prepare_snapshot_job("backup", self.root, self.root / "bad.zip")
            spawn.assert_not_called()

    def test_runtime_copy_failure_does_not_spawn_helper(self):
        with mock.patch.object(worker, "_prepare_runtime", side_effect=OSError("copy failed")), \
                mock.patch.object(snapshot, "guard_processes"), \
                mock.patch.object(worker.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(OSError, "copy failed"):
                worker.prepare_snapshot_job("backup", self.root, self.archive)
            spawn.assert_not_called()

    def test_temporary_directory_inside_root_refused_before_creation(self):
        with mock.patch.object(worker.tempfile, "gettempdir", return_value=str(self.root)), \
                mock.patch.object(worker.tempfile, "mkdtemp") as create:
            with self.assertRaisesRegex(snapshot.SnapshotError, "outside"):
                worker.prepare_snapshot_job("backup", self.root, self.archive)
            create.assert_not_called()

    def test_unapproved_late_worker_never_starts_after_parent_exit(self):
        request = self.request()
        with mock.patch.object(worker, "_validate_isolation"), mock.patch.object(worker, "ParentWaiter"), \
                mock.patch.object(snapshot, "backup") as backup, mock.patch.object(worker, "_notify"):
            self.assertEqual(worker.worker_main(str(request)), 1)
            backup.assert_not_called()
        result = json.loads((self.job / "result.json").read_text())
        self.assertIn("never approved", result["error"])

    def test_notification_can_be_suppressed_without_suppressing_result(self):
        request = self.request(notify=False)
        (self.job / "execute.json").write_text(json.dumps({"token": "a" * 32}), encoding="utf-8")
        with mock.patch.object(worker, "_validate_isolation"), mock.patch.object(worker, "ParentWaiter"), \
                mock.patch.object(snapshot, "backup", return_value={}), mock.patch.object(worker, "_notify") as notify:
            self.assertEqual(worker.worker_main(str(request)), 0)
            notify.assert_not_called()
        self.assertEqual(json.loads((self.job / "result.json").read_text())["status"], "complete")

    def test_notification_preference_requires_boolean(self):
        request = self.request(notify="false")
        with self.assertRaisesRegex(snapshot.SnapshotError, "notification"):
            worker._load_request(request)

    def test_prepare_timeout_marks_waiting_job_cancelled(self):
        process = mock.Mock()
        process.poll.return_value = None
        job = self.parent / "waiting-job"
        job.mkdir()
        with mock.patch.object(worker.tempfile, "mkdtemp", return_value=str(job)), \
                mock.patch.object(snapshot, "guard_processes"), \
                mock.patch.object(worker.subprocess, "Popen", return_value=process), \
                mock.patch.object(worker, "READY_TIMEOUT", 0):
            with self.assertRaisesRegex(snapshot.SnapshotError, "did not become ready"):
                worker.prepare_snapshot_job("backup", self.root, self.archive)
        self.assertTrue((job / "cancel.json").is_file())

    def test_cancelled_job_does_not_wait_or_invoke_engine(self):
        request = self.request()
        (self.job / "cancel.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(worker, "_validate_isolation"), mock.patch.object(worker, "ParentWaiter") as waiter, \
                mock.patch.object(snapshot, "backup") as backup, mock.patch.object(worker, "_notify"):
            self.assertEqual(worker.worker_main(str(request)), 1)
            waiter.assert_not_called()
            backup.assert_not_called()

    def test_guard_exempts_only_worker_own_pid_not_other_launchers(self):
        own = os.getpid()
        output = f'"XXMI Launcher.exe","{own}","Console","1","1 K"\n"XXMI Launcher.exe","{own + 1}","Console","1","1 K"\n'
        query = subprocess.CompletedProcess([], 0, output, "")
        with mock.patch.object(snapshot.os, "name", "nt"), \
                mock.patch.object(snapshot.subprocess, "run", return_value=query), \
                mock.patch.object(snapshot, "_WORKER_PID", own):
            with self.assertRaisesRegex(snapshot.SnapshotError, "XXMI Launcher.exe"):
                snapshot.guard_processes({})
            query.stdout = output.splitlines()[0]
            snapshot.guard_processes({})
        self.assertIsNone(snapshot._WORKER_PID)

    def test_prepare_guard_ignores_own_launcher_but_blocks_game(self):
        own = os.getpid()
        output = f'"XXMI Launcher.exe","{own}","Console","1","1 K"\n"ZenlessZoneZero.exe","{own + 1}","Console","1","1 K"\n'
        query = subprocess.CompletedProcess([], 0, output, "")
        with mock.patch.object(snapshot.os, "name", "nt"), \
                mock.patch.object(snapshot.subprocess, "run", return_value=query):
            with self.assertRaisesRegex(snapshot.SnapshotError, "ZenlessZoneZero.exe"):
                snapshot.guard_processes({}, ignore_current_launcher=True)
            query.stdout = output.splitlines()[0]
            snapshot.guard_processes({}, ignore_current_launcher=True)
            with self.assertRaisesRegex(snapshot.SnapshotError, "XXMI Launcher.exe"):
                snapshot.guard_processes({})

    @unittest.skipUnless(sys.platform.startswith("linux"), "source subprocess lifecycle test uses Linux /proc")
    def test_source_subprocess_backup_and_restore_wait_for_parent_exit(self):
        module_root = Path(worker.__file__).resolve().parents[1]
        script = (
            "import json,sys; from pathlib import Path; from core.mod_state_worker import prepare_snapshot_job; "
            "job=prepare_snapshot_job(sys.argv[1],Path(sys.argv[2]),Path(sys.argv[3])); "
            "print(json.dumps({'result_path':str(job['result_path']),'job_dir':str(job['job_dir'])}),flush=True); "
            "sys.stdin.readline()"
        )
        environment = dict(os.environ, PYTHONPATH=str(module_root))
        for operation in ("backup", "restore"):
            with self.subTest(operation=operation):
                if operation == "restore":
                    self.state.write_bytes(b"new live settings")
                    (self.zzmi / "Mods" / "stale.ini").write_bytes(b"stale")
                parent = subprocess.Popen([sys.executable, "-c", script, operation, str(self.root), str(self.archive)],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, env=environment)
                try:
                    line = parent.stdout.readline()
                    self.assertTrue(line, parent.stderr.read() if parent.poll() is not None else "no readiness report")
                    job = json.loads(line)
                    self.addCleanup(shutil.rmtree, job["job_dir"], True)
                    time.sleep(0.15)
                    self.assertFalse(Path(job["result_path"]).exists(), "worker acted before initiating parent exit")
                    if operation == "backup":
                        self.assertFalse(self.archive.exists())
                    else:
                        self.assertEqual(self.state.read_bytes(), b"new live settings")
                    _, stderr = parent.communicate("\n", timeout=10)
                    self.assertEqual(parent.returncode, 0, stderr)
                    deadline = time.monotonic() + 10
                    report = Path(job["result_path"])
                    while not report.exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(report.exists(), "detached worker did not report completion")
                    result = json.loads(report.read_text())
                    self.assertEqual(result["status"], "complete", result)
                finally:
                    if parent.poll() is None:
                        # Let the test parent exit normally; never terminate it.
                        parent.communicate("\n", timeout=10)
                if operation == "restore":
                    self.assertEqual(self.state.read_bytes(), b"[Constants]\r\n$skin = 4\r\n")
                    self.assertFalse((self.zzmi / "Mods" / "stale.ini").exists())
                    self.assertTrue(Path(result["detail"]["rollback"]).is_dir())


if __name__ == "__main__":
    unittest.main()
