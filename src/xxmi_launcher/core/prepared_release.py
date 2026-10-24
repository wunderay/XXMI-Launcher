"""Authenticated isolated development releases; never alters package trust or Mods.

Selection stages a publisher-signed archive. Deployment remains MigotoPackage's
normal verified pre-launch path. Rollback restores only explicitly managed files.
"""
import hashlib
import base64
import io
import json
import os
import shutil
import tempfile
import zipfile
import uuid
from pathlib import Path

from core.prepared_loading import CAPABILITY_FILE, inspect_capability

LIBRARIES = ('d3d11.dll', 'd3dcompiler_47.dll', '3dmloader.dll')
SNAPSHOT_FILES = ('d3d11.dll', 'd3dcompiler_47.dll', 'd3dx.ini')


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _plain_path(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (candidate.exists() and
                getattr(candidate.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError(f'Reparse/symlink paths are not allowed: {candidate}')


def _seal_snapshot(data):
    # OS protection authenticates local lifecycle metadata without creating or
    # changing any publisher signing key. No insecure non-Windows fallback.
    import win32crypt
    return win32crypt.CryptProtectData(data, 'XXMI development lifecycle', None, None, None, 0)


def _open_snapshot(data):
    import win32crypt
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


def _windows_kernel():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD)
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


class _StagedFile:
    """Own the staged inode until commit, including recovery replacements.

    On Windows the creation handle denies other writers and deletion. Rename
    that same handle; closing it then renaming a path reopens a substitution
    window even if a hash was checked immediately before the rename.
    """
    def __init__(self, directory, data):
        _plain_path(directory)
        self.path, self.stream = None, None
        self.digest = _digest(data)
        if os.name == 'nt':
            import ctypes
            import msvcrt
            kernel = _windows_kernel()
            path = directory / ('.xxmi-' + uuid.uuid4().hex + '.tmp')
            # GENERIC_READ | GENERIC_WRITE | DELETE, FILE_SHARE_READ,
            # CREATE_NEW, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT.
            handle = kernel.CreateFileW(str(path), 0xc0010000, 1, None, 1, 0x200080, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.path = path
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(handle)
                path.unlink(missing_ok=True)
                raise
        else:
            # Portable test support. The launcher and DPAPI lifecycle require
            # Windows; only Windows supplies the protected handle contract.
            fd, name = tempfile.mkstemp(prefix='.xxmi-', suffix='.tmp', dir=directory)
            self.path = Path(name)
        try:
            self.stream = os.fdopen(fd, 'w+b')
            self.stream.write(data)
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except BaseException:
            self.close()
            raise

    def replace(self, target):
        if os.name == 'nt':
            import ctypes
            import msvcrt
            from ctypes import wintypes
            class RenameInformation(ctypes.Structure):
                _fields_ = [('ReplaceIfExists', wintypes.BOOL),
                            ('RootDirectory', wintypes.HANDLE),
                            ('FileNameLength', wintypes.DWORD),
                            ('FileName', wintypes.WCHAR * 1)]
            # Win32's DOS-path conversion requires a NUL-terminated wide
            # string even though FileNameLength excludes that terminator.
            name = (str(target.absolute()) + '\0').encode('utf-16-le')
            size = RenameInformation.FileName.offset + len(name)
            information = ctypes.create_string_buffer(max(size, ctypes.sizeof(RenameInformation)))
            header = RenameInformation.from_buffer(information)
            header.ReplaceIfExists = True
            header.FileNameLength = len(name) - 2
            ctypes.memmove(ctypes.addressof(information) + RenameInformation.FileName.offset, name, len(name))
            handle = msvcrt.get_osfhandle(self.stream.fileno())
            if not _windows_kernel().SetFileInformationByHandle(handle, 3, information, size):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            # Catch test-fixture mutations without claiming this path provides
            # the Windows handle guarantee.
            if self.path.stat().st_ino != os.fstat(self.stream.fileno()).st_ino:
                raise ValueError('Staged file identity changed')
            self.stream.seek(0)
            if _digest(self.stream.read()) != self.digest:
                raise ValueError('Staged file contents changed')
            os.replace(self.path, target)
        self.path = None

    def close(self, *, retain=False):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.path is not None and not retain:
            self.path.unlink(missing_ok=True)
            self.path = None


def _temporary(directory, data):
    return _StagedFile(directory, data)


def _process_names():
    """Toolhelp enumerates names without opening protected/elevated processes."""
    import ctypes
    from ctypes import wintypes
    if os.name != 'nt':
        raise OSError('Process snapshot requires Windows')
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = _windows_kernel()
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for operation in (kernel.Process32FirstW, kernel.Process32NextW):
        operation.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        operation.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        names = []
        while True:
            if not entry.szExeFile:
                raise ValueError('Process snapshot contains an unknown executable name')
            names.append(entry.szExeFile)
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:  # ERROR_NO_MORE_FILES is the only successful end.
                    raise ctypes.WinError(error)
                return names
    finally:
        kernel.CloseHandle(snapshot)


def assert_process_idle(names):
    if not names or any(not name or Path(name).name != name for name in names):
        raise ValueError('Cannot verify the game is stopped: no valid executable names.')
    try:
        processes = _process_names()
        if not processes or any(not name for name in processes):
            raise ValueError('Incomplete process snapshot')
    except Exception as error:
        raise ValueError('Cannot verify process identity; development library change refused.') from error
    if {name.casefold() for name in names}.intersection(name.casefold() for name in processes):
        raise ValueError('Stop the game before changing the selected XXMI libraries.')


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    _plain_path(path)
    sealed = _seal_snapshot(json.dumps(value).encode())
    temporary = _temporary(path.parent, json.dumps({'sealed': base64.b64encode(sealed).decode()}).encode())
    try:
        temporary.replace(path)
    finally:
        temporary.close()


def _read_json(path):
    _plain_path(path)
    try:
        wrapped = json.loads(path.read_bytes())
        return json.loads(_open_snapshot(base64.b64decode(wrapped['sealed'], validate=True)))
    except Exception as error:
        raise ValueError('Lifecycle metadata authentication failed') from error


def _replace_set(importer, contents, assert_idle):
    """Prepare immutable writes and recovery files before changing any target.

    A failed replacement restores the complete state present before this call.
    Recovery files are retained if an OS failure also prevents recovery.
    """
    _plain_path(importer)
    journal = importer / '.xxmi-recovery-required.json'
    if journal.exists():
        raise ValueError('A prior library transaction requires recovery before launch.')
    prepared, recovery, changed = {}, {}, []
    retain_recovery = False
    try:
        for name, data in contents.items():
            target = importer / name
            _plain_path(target)
            recovery[name] = _temporary(importer, target.read_bytes()) if target.exists() else None
            prepared[name] = _temporary(importer, data) if data is not None else None
        _write_json(journal, {'recovery': {name: str(path.path) if path else None for name, path in recovery.items()}})
        assert_idle()
        try:
            for name, temporary in prepared.items():
                target = importer / name
                _plain_path(target)
                if temporary is None:
                    target.unlink(missing_ok=True)
                else:
                    temporary.replace(target)
                changed.append(name)
                if temporary is not None:
                    temporary.close()
        except BaseException as original:
            failures = []
            for name in reversed(changed):
                try:
                    target = importer / name
                    _plain_path(target)
                    if recovery[name] is None:
                        target.unlink(missing_ok=True)
                    else:
                        recovery[name].replace(target)
                        recovery[name].close()
                except Exception as error:
                    failures.append((name, str(error)))
            if failures:
                retain_recovery = True
                raise RuntimeError(f'Rollback recovery blocked by OS; retained recovery files {recovery}; failures {failures}') from original
            raise
    finally:
        for path in prepared.values():
            if path is not None:
                path.close()
        for path in recovery.values():
            if path is not None:
                path.close(retain=retain_recovery)
        if not retain_recovery:
            journal.unlink(missing_ok=True)


class DevelopmentRelease:
    def __init__(self, root: Path, security):
        self.root, self.security = root, security
        self.selection_path = root / 'selection.json'

    @property
    def selected(self):
        return self.selection_path.exists()

    def verify(self, path: Path, *, version: str, stage: str):
        _plain_path(path)
        for name in (*LIBRARIES, CAPABILITY_FILE, 'Manifest.json'):
            _plain_path(path / name)
        manifest = json.loads((path / 'Manifest.json').read_text(encoding='utf-8'))
        if stage != 'development' or not version or manifest['version'] != version:
            raise ValueError('Development package version/stage gate failed')
        signatures = manifest['signatures']
        for name in (*LIBRARIES, CAPABILITY_FILE):
            if not self.security.verify(signatures.get(name), (path / name).read_bytes()):
                raise ValueError(f'Development package signature invalid: {name}')
        marker = json.loads((path / CAPABILITY_FILE).read_text(encoding='utf-8'))
        if marker.get('release_stage') != stage or marker.get('package_version') != version:
            raise ValueError('Authenticated capability version/stage gate failed')
        capability = inspect_capability(path / 'd3d11.dll', path / CAPABILITY_FILE,
            lambda item, data: self.security.verify(signatures.get(item.name), data))
        if not capability.supported:
            raise ValueError(capability.reason)
        return capability

    def select(self, archive: Path, signature: str, *, version: str, importer: Path, stage: str = 'development'):
        """No active library writes. Uses the package's existing publisher key."""
        data = archive.read_bytes()
        if not self.security.verify(signature, data):
            raise ValueError('Development archive signature invalid')
        digest = _digest(data)
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            destination = Path(temporary)
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                names = package.namelist()
                allowed = {*LIBRARIES, CAPABILITY_FILE, 'Manifest.json'}
                if len(names) != len(set(names)) or set(names) != allowed:
                    raise ValueError('Development archive must contain only managed package files')
                if sum(item.file_size for item in package.infolist()) > 256 * 1024 * 1024:
                    raise ValueError('Development archive exceeds 256 MiB')
                # Capture the members of the authenticated outer archive. A
                # publisher signature on an individual DLL does not establish
                # that it belongs to this archive/version.
                payload = {name: package.read(name) for name in names}
            hashes = {name: _digest(value) for name, value in payload.items()}
            for name, value in payload.items():
                (destination / name).write_bytes(value)
            capability = self.verify(destination, version=version, stage=stage)
            pinned = self.root / digest
            if pinned.exists():
                # Never repair/replace a pin silently: a damaged pin requires explicit removal.
                self.verify(pinned, version=version, stage=stage)
            else:
                shutil.copytree(destination, pinned)
            if ({item.name for item in pinned.iterdir()} != set(payload) or
                    any((pinned / name).read_bytes() != value for name, value in payload.items())):
                raise ValueError('Pinned development package differs from authenticated archive')
            _write_json(self.selection_path, dict(archive_sha256=digest, version=version, stage=stage, hashes=hashes, importer=str(importer.resolve())))
        return capability

    def selection(self):
        return _read_json(self.selection_path)

    def resolve(self, importer: Path | None = None):
        value = self.selection()
        if importer is not None and (importer / '.xxmi-recovery-required.json').exists():
            raise ValueError('A prior library transaction requires recovery before launch.')
        if importer is not None and value.get('importer') != str(importer.resolve()):
            raise ValueError('Development package is pinned to a different importer; roll back there first')
        digest = value['archive_sha256']
        if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise ValueError('Invalid development package pin')
        path = self.root / digest
        self.verify(path, version=value['version'], stage=value['stage'])
        expected = {*LIBRARIES, CAPABILITY_FILE, 'Manifest.json'}
        if (set(value['hashes']) != expected or {item.name for item in path.iterdir()} != expected or
                any(_digest((path / name).read_bytes()) != value['hashes'][name] for name in expected)):
            raise ValueError('Pinned development package changed')
        return path

    def snapshot(self, importer: Path, manifest: Path):
        """Keep the first known-good state until rollback, including original config."""
        destination = self.root / ('rollback-' + _digest(str(importer.resolve()).encode()))
        if destination.exists() and self.selected:
            return destination
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            temporary = Path(temporary)
            metadata = {}
            _plain_path(manifest)
            signatures = json.loads(manifest.read_text(encoding='utf-8'))['signatures']
            for name in SNAPSHOT_FILES:
                source = importer / name
                _plain_path(source)
                if not source.exists():
                    metadata[name] = None
                    continue
                data = source.read_bytes()
                if name.endswith('.dll') and not self.security.verify(signatures.get(name), data):
                    raise ValueError(f'Cannot back up unauthenticated prior library: {name}')
                (temporary / name).write_bytes(data)
                metadata[name] = dict(sha256=_digest(data), signature=signatures.get(name))
            (temporary / 'Manifest.json').write_bytes(manifest.read_bytes())
            _write_json(temporary / 'snapshot.json', metadata)
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(temporary, destination)
        return destination

    def rollback(self, importer: Path, *, assert_idle):
        """Caller must prove the target process is stopped; no Mods paths are accepted."""
        assert_idle()
        backup = self.root / ('rollback-' + _digest(str(importer.resolve()).encode()))
        metadata = _read_json(backup / 'snapshot.json')
        contents = {}
        if set(metadata) != set(SNAPSHOT_FILES):
            raise ValueError('Invalid rollback file set')
        # Verify every file before making any changes.
        for name, record in metadata.items():
            if record is None:
                contents[name] = None
                continue
            _plain_path(backup / name)
            data = (backup / name).read_bytes()
            if _digest(data) != record['sha256'] or (name.endswith('.dll') and not self.security.verify(record['signature'], data)):
                raise ValueError(f'Rollback verification failed: {name}')
            contents[name] = data
        _replace_set(importer, contents, assert_idle)
        return backup

    def deploy(self, importer: Path, *, assert_idle):
        path = self.resolve(importer)
        selection = self.selection()
        # Authenticate the exact immutable bytes used by the transaction rather
        # than paths that can be replaced between verification and deployment.
        payload = {}
        for name in (*LIBRARIES, CAPABILITY_FILE, 'Manifest.json'):
            _plain_path(path / name)
            payload[name] = (path / name).read_bytes()
            if _digest(payload[name]) != selection['hashes'][name]:
                raise ValueError('Pinned development package changed before deployment')
        manifest = json.loads(payload['Manifest.json'])
        for name in (*LIBRARIES, CAPABILITY_FILE):
            if not self.security.verify(manifest['signatures'].get(name), payload[name]):
                raise ValueError(f'Development deployment signature invalid: {name}')
        marker = json.loads(payload[CAPABILITY_FILE])
        if marker['dll_sha256'] != _digest(payload['d3d11.dll']):
            raise ValueError('Development deployment DLL identity mismatch')
        _replace_set(importer, {name: payload[name] for name in SNAPSHOT_FILES if name.endswith('.dll')}, assert_idle)
        return {name: manifest['signatures'][name] for name in SNAPSHOT_FILES if name.endswith('.dll')}
