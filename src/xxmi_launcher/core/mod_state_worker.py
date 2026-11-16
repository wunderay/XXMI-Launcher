"""Detached mod-state jobs. This module deliberately imports no launcher UI/state.

The UI prepares a runtime outside the captured root, waits for readiness, then
exits normally. The helper waits for that exact process to exit before invoking
the offline engine. Results remain in the external temporary job directory.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

if __package__:
    from . import mod_state_snapshot as snapshot
else:
    # -I -S intentionally removes the copied script directory from sys.path.
    # Load only its adjacent, isolated engine; never add the original repo path.
    _spec = importlib.util.spec_from_file_location(
        "mod_state_snapshot", Path(__file__).with_name("mod_state_snapshot.py"))
    snapshot = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(snapshot)

WAIT_TIMEOUT = 120.0
READY_TIMEOUT = 30.0


def _write_json(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _kernel():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _windows_creation(kernel, handle) -> str:
    import ctypes
    from ctypes import wintypes
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)


def _proc_identity(pid: int) -> tuple[str, str] | None:
    """Linux-only test/source support: exact birth marker and current state."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise snapshot.SnapshotError(f"Cannot verify initiating process identity: {exc}") from exc
    fields = data.rsplit(")", 1)[1].split()
    if len(fields) < 20:
        raise snapshot.SnapshotError("Cannot interpret initiating process identity.")
    return fields[19], fields[0]


def process_identity(pid: int) -> str:
    if os.name == "nt":
        import ctypes
        kernel = _kernel()
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return _windows_creation(kernel, handle)
        finally:
            kernel.CloseHandle(handle)
    identity = _proc_identity(pid)
    if identity is None:
        raise snapshot.SnapshotError("Initiating process already exited before preparation.")
    return identity[0]


class ParentWaiter:
    """Pin an exact Windows process handle before declaring worker readiness."""
    def __init__(self, pid: int, creation: str):
        self.pid, self.creation, self.handle, self.kernel = pid, creation, None, None
        if pid == os.getpid():
            raise snapshot.SnapshotError("Worker cannot wait for itself.")
        if os.name == "nt":
            import ctypes
            self.kernel = _kernel()
            self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if _windows_creation(self.kernel, self.handle) != creation:
                    raise snapshot.SnapshotError("Initiating process identity changed; refusing PID reuse.")
            except BaseException:
                self.close()
                raise
        else:
            identity = _proc_identity(pid)
            if identity is None or identity[0] != creation:
                raise snapshot.SnapshotError("Initiating process identity changed before readiness.")

    def wait(self, timeout: float):
        deadline = time.monotonic() + timeout
        while True:
            if self.handle is not None:
                status = self.kernel.WaitForSingleObject(self.handle, 0)
                if status == 0:  # WAIT_OBJECT_0
                    return
                if status != 258:  # WAIT_TIMEOUT
                    raise snapshot.SnapshotError("Failed waiting for initiating launcher process.")
            else:
                identity = _proc_identity(self.pid)
                if identity is None or identity[0] != self.creation or identity[1] == "Z":
                    return  # Original process is gone, including a reused PID.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise snapshot.SnapshotError("Timed out waiting for the initiating launcher to exit; no snapshot operation was started. Close it normally and retry.")
            time.sleep(min(0.1, remaining))

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _outside(path: Path, root: Path, description: str):
    if snapshot.inside(path, root) or snapshot.inside(path.resolve(), root):
        raise snapshot.SnapshotError(f"{description} must be outside the captured launcher root: {path}")


def preflight(operation: str, root: Path, archive: Path) -> dict:
    """Read-only preparation; the initiating launcher is intentionally still open."""
    if operation not in {"backup", "restore"}:
        raise snapshot.SnapshotError("Unknown mod-state snapshot operation.")
    root, archive = snapshot.checked_path(root), snapshot.checked_path(archive)
    _outside(archive, root, "Snapshot archive")
    if not archive.parent.is_dir():
        raise snapshot.SnapshotError(f"Archive directory does not exist: {archive.parent}")
    if operation == "backup":
        if archive.exists():
            raise snapshot.SnapshotError(f"Archive already exists; refusing to overwrite: {archive}")
        detail = snapshot.inspect_root(root, check_processes=False)
        snapshot.guard_processes(snapshot._read_json(root / snapshot.CONFIG), root, ignore_current_launcher=True)
    else:
        detail = snapshot.verify(archive)
        if os.path.normcase(str(root)) != os.path.normcase(str(snapshot.checked_path(detail["original_root"]))):
            raise snapshot.SnapshotError(f"Restore is restricted to the original root: {detail['original_root']}")
        if root.exists():
            snapshot.inventory(root)
            if (root / snapshot.CONFIG).exists():
                snapshot.guard_processes(snapshot._read_json(root / snapshot.CONFIG), root,
                                         ignore_current_launcher=True)
        with zipfile.ZipFile(archive) as source:
            archived_config = json.loads(source.read("payload/" + snapshot.CONFIG).decode("utf-8-sig"))
        snapshot.guard_processes(archived_config, ignore_current_launcher=True)
        if not root.parent.is_dir():
            raise snapshot.SnapshotError(f"Restore parent directory does not exist: {root.parent}")
    return detail


def _child_environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        upper = key.upper()
        if upper.startswith("_PYI_") or upper in {"_MEIPASS", "_MEIPASS2", "PYTHONHOME", "PYTHONPATH"}:
            environment.pop(key, None)
    # Official PyInstaller bootloader reset for an independently surviving child.
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    inherited_bundle = getattr(sys, "_MEIPASS", None)
    cleaned = []
    for value in environment.get("PATH", "").split(os.pathsep):
        if not value:
            continue
        path = Path(os.path.abspath(value))
        if snapshot.inside(path, root) or (inherited_bundle and snapshot.inside(path, Path(inherited_bundle))):
            continue
        cleaned.append(value)
    environment["PATH"] = os.pathsep.join(cleaned)
    if "LD_LIBRARY_PATH_ORIG" in environment:
        environment["LD_LIBRARY_PATH"] = environment.pop("LD_LIBRARY_PATH_ORIG")
    elif inherited_bundle:
        environment.pop("LD_LIBRARY_PATH", None)
    return environment


def _prepare_runtime(job: Path, root: Path) -> tuple[list[str], bool]:
    runtime = job / "runtime"
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        origin = snapshot.checked_path(executable.parent)
        bundle = getattr(sys, "_MEIPASS", None)
        if (origin.name.casefold() != "bin" or origin.parent.name.casefold() != "resources" or
                executable.name.casefold() not in {"xxmi launcher.exe", "xxmi launcher no console.exe"} or
                not (origin / "_internal").is_dir() or not bundle or
                Path(bundle).resolve() != (origin / "_internal").resolve()):
            raise snapshot.SnapshotError("Unsupported frozen launcher layout. Snapshot helper requires the complete PyInstaller Resources/Bin onedir runtime; no files were changed.")
        # Enumerate first to reject symlinks/reparse points and copying a mods root.
        snapshot.inventory(origin)
        if (origin / snapshot.CONFIG).exists() or (origin / "ZZMI").exists() or (origin / "Mods").exists():
            raise snapshot.SnapshotError("Runtime directory contains launcher/mod data; refusing an unsafe helper copy.")
        shutil.copytree(origin, runtime, copy_function=shutil.copy2)
        return [str(runtime / executable.name)], True
    if "__compiled__" in globals():
        raise snapshot.SnapshotError("This compiled runtime is unsupported by the detached snapshot helper.")
    _outside(executable, root, "Python interpreter")
    runtime.mkdir()
    shutil.copy2(Path(__file__), runtime / "mod_state_worker.py")
    shutil.copy2(Path(snapshot.__file__), runtime / "mod_state_snapshot.py")
    return [str(executable), "-I", "-S", str(runtime / "mod_state_worker.py")], False


def prepare_snapshot_job(operation: str, root: Path, archive: Path, *, notify: bool = True) -> dict:
    """Prepare and start a waiting helper; caller exits only after this returns.

    Raises before the caller closes on preflight/copy/start/readiness failure.
    No process is terminated. A helper that cannot observe parent exit times out.
    """
    if type(notify) is not bool:
        raise snapshot.SnapshotError("Snapshot notification preference must be boolean.")
    root, archive = snapshot.checked_path(root), snapshot.checked_path(archive)
    preflight(operation, root, archive)
    temporary_root = snapshot.checked_path(tempfile.gettempdir())
    _outside(temporary_root, root, "Temporary helper directory")
    job = snapshot.checked_path(tempfile.mkdtemp(prefix="xxmi-mod-state-", dir=temporary_root))
    try:
        _outside(job, root, "Temporary helper job")
        command, frozen = _prepare_runtime(job, root)
        request_path, result_path = job / "request.json", job / "result.json"
        request = {"version": 1, "operation": operation, "root": str(root), "archive": str(archive),
                   "parent_pid": os.getpid(), "parent_creation": process_identity(os.getpid()),
                   "wait_timeout": WAIT_TIMEOUT, "token": uuid.uuid4().hex, "frozen": frozen,
                   "notify": notify}
        _write_json(request_path, request)
        kwargs = {"cwd": str(job), "env": _child_environment(root), "stdin": subprocess.DEVNULL,
                  "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command + ["--mod-state-worker", str(request_path)], **kwargs)
    except BaseException:
        shutil.rmtree(job, ignore_errors=True)
        raise
    ready_path = job / "ready.json"
    deadline = time.monotonic() + READY_TIMEOUT
    try:
        while time.monotonic() < deadline:
            if ready_path.exists():
                ready = json.loads(ready_path.read_text(encoding="utf-8"))
                if ready.get("token") != request["token"] or ready.get("pid") != process.pid:
                    raise snapshot.SnapshotError(f"Unexpected helper readiness response. Launcher remains open; job: {job}")
                _write_json(job / "execute.json", {"token": request["token"]})
                return {"request_path": request_path, "result_path": result_path,
                        "ready_path": ready_path, "job_dir": job, "process": process}
            if process.poll() is not None:
                error = "Helper exited before readiness."
                if result_path.exists():
                    error = json.loads(result_path.read_text(encoding="utf-8")).get("error", error)
                raise snapshot.SnapshotError(f"{error} Launcher remains open. Details: {result_path}")
            time.sleep(0.05)
        raise snapshot.SnapshotError(f"Snapshot helper did not become ready; launcher remains open. Temporary job: {job}")
    except BaseException:
        # A late helper must not start an operation after preparation failed.
        _write_json(job / "cancel.json", {"reason": "Preparation failed before shutdown"})
        raise


def _load_request(path: Path) -> dict:
    if path.stat().st_size > 64 * 1024:
        raise snapshot.SnapshotError("Snapshot job request is too large.")
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict) or request.get("version") != 1 or request.get("operation") not in {"backup", "restore"}:
        raise snapshot.SnapshotError("Invalid snapshot job request.")
    if not isinstance(request.get("parent_pid"), int) or request["parent_pid"] <= 0:
        raise snapshot.SnapshotError("Invalid initiating process ID.")
    if not isinstance(request.get("parent_creation"), str) or not request["parent_creation"].isdigit():
        raise snapshot.SnapshotError("Missing initiating process creation time.")
    timeout = request.get("wait_timeout")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise snapshot.SnapshotError("Invalid bounded parent wait timeout.")
    if not isinstance(request.get("token"), str) or len(request["token"]) != 32:
        raise snapshot.SnapshotError("Invalid snapshot job token.")
    if type(request.get("frozen")) is not bool:
        raise snapshot.SnapshotError("Invalid snapshot runtime type.")
    request.setdefault("notify", True)
    if type(request["notify"]) is not bool:
        raise snapshot.SnapshotError("Invalid snapshot notification preference.")
    for key in ("root", "archive"):
        if not isinstance(request.get(key), str) or not os.path.isabs(request[key]):
            raise snapshot.SnapshotError(f"Snapshot {key} must be an explicit absolute path.")
    return request


def _validate_isolation(job: Path, root: Path):
    _outside(job, root, "Temporary helper job")
    runtime = snapshot.checked_path(job / "runtime")
    # Merely being outside root is insufficient ownership for result writes or
    # cleanup: this exact job must contain the code currently executing.
    for module in (Path(__file__), Path(snapshot.__file__)):
        if not snapshot.inside(module.absolute(), runtime) or not snapshot.inside(module.resolve(), runtime):
            raise snapshot.SnapshotError("Worker modules do not belong to this isolated job/runtime; refusing reporting or cleanup.")
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        bundle = getattr(sys, "_MEIPASS", None)
        if executable.parent != runtime or not bundle or Path(bundle).resolve() != runtime / "_internal":
            raise snapshot.SnapshotError("Frozen worker runtime does not belong to this isolated job.")
    paths = [job, Path.cwd(), Path(sys.executable), Path(__file__), Path(snapshot.__file__)]
    paths.extend(Path(value) for value in sys.path if value)
    paths.extend(Path(value) for value in (sys.prefix, sys.base_prefix) if value)
    if getattr(sys, "_MEIPASS", None):
        paths.append(Path(sys._MEIPASS))
    for path in paths:
        _outside(path.absolute(), root, "Worker runtime or import path")


def _notify(success: bool, message: str):
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    user32.MessageBoxW.restype = ctypes.c_int
    user32.MessageBoxW(None, message, "XXMI Mod State Snapshot", 0x40 if success else 0x10)


def worker_main(request_path: str) -> int:
    """Early entry point, before launcher path setup/logging/GUI initialization."""
    waiter = None
    request, result, job, safe_job = {}, None, None, False
    try:
        path = snapshot.checked_path(request_path)
        job = path.parent
        if path.name != "request.json":
            raise snapshot.SnapshotError("Expected an isolated request.json job file.")
        request = _load_request(path)
        root = snapshot.checked_path(request["root"])
        _outside(job, root, "Temporary helper job")
        _validate_isolation(job, root)
        if request["frozen"] != bool(getattr(sys, "frozen", False)):
            raise snapshot.SnapshotError("Snapshot request runtime type differs from the executing helper.")
        safe_job = True
        if (job / "cancel.json").exists():
            raise snapshot.SnapshotError("Snapshot job was cancelled during preparation.")
        waiter = ParentWaiter(request["parent_pid"], request["parent_creation"])
        _write_json(job / "ready.json", {"pid": os.getpid(), "token": request["token"]})
        waiter.wait(request["wait_timeout"])
        if (job / "cancel.json").exists():
            raise snapshot.SnapshotError("Snapshot job was cancelled; no operation was started.")
        execute = job / "execute.json"
        if not execute.is_file() or json.loads(execute.read_text(encoding="utf-8")).get("token") != request["token"]:
            raise snapshot.SnapshotError("Snapshot helper was never approved by the ready initiating launcher; no operation was started.")
        # This is the only process exemption, bound again in guard_processes.
        snapshot._WORKER_PID = os.getpid()
        if request["operation"] == "backup":
            detail = snapshot.backup(root, request["archive"])
        else:
            detail = snapshot.restore(request["archive"], root, apply=True)
        result = {"status": "complete", "operation": request["operation"], "detail": detail}
        code = 0
    except Exception as exc:
        result = {"status": "error", "operation": request.get("operation"), "error": str(exc)}
        code = 1
    finally:
        if waiter:
            waiter.close()
        snapshot._WORKER_PID = None
    if not safe_job:
        if request.get("notify", True):
            _notify(False, result["error"])
        return code
    # Keep metadata/results for recovery. Frozen files can remain loaded and
    # cannot be safely removed by their own running process on Windows.
    result["job_dir"] = str(job)
    result["runtime_retained"] = bool(request.get("frozen"))
    try:
        if not request.get("frozen"):
            shutil.rmtree(job / "runtime", ignore_errors=True)
            result["runtime_retained"] = (job / "runtime").exists()
        _write_json(job / "result.json", result)
    except OSError as exc:
        result["error"] = result.get("error", "Operation completed") + f"; cannot persist job result: {exc}"
        code = 1
    message = (f"{request.get('operation', 'Snapshot').capitalize()} completed.\n" if code == 0 else f"Snapshot failed: {result.get('error')}\n")
    if result.get("detail", {}).get("rollback"):
        message += f"Previous installation retained at:\n{result['detail']['rollback']}\n"
    message += f"Details and recovery information:\n{job / 'result.json'}\nReopen XXMI manually when ready."
    if request.get("notify", True):
        _notify(code == 0, message)
    return code


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--mod-state-worker":
        raise SystemExit(worker_main(sys.argv[2]))
    raise SystemExit("This is an internal detached XXMI snapshot worker.")
