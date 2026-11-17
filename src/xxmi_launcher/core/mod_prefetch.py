"""Warm the OS file cache with the mod files the XXMI DLL is about to load.

The DLL reads every included INI and every resource they reference (buffers, textures) while
the game starts, often from a cold disk cache and through a first-open antivirus scan. The
launcher already waits for the game window, so it can read the same files first, in the same
order, from a background-priority thread. The game then reads them from memory.

Costs are bounded: one reusable 1 MB buffer, background CPU/IO priority on Windows, files are
read once, and nothing beyond a share of the currently available memory is read (the OS cache
would otherwise evict the start of the list before the game uses it).
"""
import os
import sys
import time
import ctypes
import fnmatch
import logging
import threading

from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
# Share of currently available memory the warmed files may occupy, and an absolute cap
MEMORY_SHARE = 0.25
MAX_BYTES = 8 * 1024 ** 3

THREAD_MODE_BACKGROUND_BEGIN = 0x00010000
THREAD_MODE_BACKGROUND_END = 0x00020000


@dataclass
class IncludeSettings:
    files: list[Path] = field(default_factory=list)
    folders: list[Path] = field(default_factory=list)
    recursive_folders: list[Path] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)


def read_include_settings(d3dx_ini_path: Path) -> IncludeSettings:
    """Parse the [Include] section the way 3DMigoto uses it (paths relative to d3dx.ini)."""
    settings = IncludeSettings()
    base = d3dx_ini_path.parent
    section = None
    for raw_line in d3dx_ini_path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw_line.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('['):
            section = line.strip('[]').strip().lower()
            continue
        if section != 'include' or '=' not in line:
            continue
        key, value = (part.strip() for part in line.split('=', 1))
        key = key.lower()
        value = value.strip('"').replace('\\', os.sep)
        if key == 'include':
            settings.files.append(base / value)
        elif key == 'include_directory':
            settings.folders.append(base / value)
        elif key == 'include_recursive':
            settings.recursive_folders.append(base / value)
        elif key == 'exclude_recursive':
            settings.exclude_patterns.append(value.lower())
    return settings


def _is_excluded(name: str, patterns: list[str]) -> bool:
    name = name.lower()
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


MAX_LINE_BYTES = 64 * 1024


def _stopped(stop: threading.Event | None) -> bool:
    return stop is not None and stop.is_set()


def _iter_ini_files(folder: Path, patterns: list[str], recursive: bool, stop: threading.Event | None = None):
    pending = [folder]
    visited = set()
    while pending and not _stopped(stop):
        current = pending.pop()
        try:
            real = os.path.realpath(current)
            if real in visited:
                continue
            visited.add(real)
            with os.scandir(current) as listing:
                entries = sorted(listing, key=lambda entry: entry.name.lower())
        except OSError:
            continue
        subfolders = []
        for entry in entries:
            try:
                if entry.is_dir():
                    if recursive and not _is_excluded(entry.name, patterns):
                        subfolders.append(Path(entry.path))
                elif entry.name.lower().endswith('.ini') and not _is_excluded(entry.name, patterns):
                    yield entry.path
            except OSError:
                continue
        pending.extend(reversed(subfolders))


def _referenced_files(ini_path: str, stop: threading.Event | None = None):
    """Files named by `filename = ...` options, relative to the INI's folder.

    Streams the INI with bounded line reads (an oversized line is skipped in pieces), so parsing
    never holds a whole file in memory."""
    try:
        file = open(ini_path, 'rb')
    except OSError:
        return
    with file:
        while not _stopped(stop):
            try:
                raw_line = file.readline(MAX_LINE_BYTES)
            except OSError:
                return
            if not raw_line:
                return
            if len(raw_line) == MAX_LINE_BYTES and not raw_line.endswith(b'\n'):
                # Over-long line: discard the rest of it
                while (rest := file.readline(MAX_LINE_BYTES)) and not rest.endswith(b'\n'):
                    pass
                continue
            line = raw_line.decode('utf-8', errors='replace').strip()
            if line[:8].lower() != 'filename':
                continue
            key, separator, value = line.partition('=')
            if not separator or key.strip().lower() != 'filename':
                continue
            value = value.strip().strip('"')
            if value:
                yield os.path.join(os.path.dirname(ini_path), value.replace('\\', os.sep))


def iter_mod_files(d3dx_ini_path: Path, stop: threading.Event | None = None):
    """INI and resource files in the order the DLL loads them, without duplicates.

    A generator: folders are listed and INIs parsed only as the caller consumes it, so a stop
    request or an exhausted budget ends collection too."""
    settings = read_include_settings(d3dx_ini_path)

    def ini_files():
        yield from (str(path) for path in settings.files if path.is_file())
        for folder in settings.folders:
            yield from _iter_ini_files(folder, settings.exclude_patterns, recursive=False, stop=stop)
        for folder in settings.recursive_folders:
            yield from _iter_ini_files(folder, settings.exclude_patterns, recursive=True, stop=stop)

    seen = set()
    for ini_path in ini_files():
        if _stopped(stop):
            return
        for path in (ini_path, *_referenced_files(ini_path, stop)):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                yield path


def collect_mod_files(d3dx_ini_path: Path, stop: threading.Event | None = None) -> list[str]:
    return list(iter_mod_files(d3dx_ini_path, stop))


def memory_budget() -> int:
    try:
        import psutil
        available = psutil.virtual_memory().available
    except Exception:
        return 0
    return int(min(available * MEMORY_SHARE, MAX_BYTES))


@dataclass
class PrefetchStats:
    files: int = 0
    bytes: int = 0
    skipped_files: int = 0
    seconds: float = 0.0
    cpu_seconds: float = 0.0
    completed: bool = False


def _enter_background_mode():
    if sys.platform == 'win32':
        kernel32 = ctypes.WinDLL('kernel32')
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_MODE_BACKGROUND_BEGIN)


def _leave_background_mode():
    if sys.platform == 'win32':
        kernel32 = ctypes.WinDLL('kernel32')
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_MODE_BACKGROUND_END)


def prefetch_files(paths, budget: int, stop: threading.Event | None = None) -> PrefetchStats:
    """Read files (any iterable, consumed lazily) in order until done, stopped or the budget
    is used up; collection behind a generator ends at the same point."""
    stats = PrefetchStats()
    started = time.perf_counter()
    cpu_started = time.thread_time()
    buffer = bytearray(CHUNK_SIZE)
    view = memoryview(buffer)
    for path in paths:
        if _stopped(stop) or stats.bytes >= budget:
            break
        try:
            size = os.stat(path).st_size
        except OSError:
            stats.skipped_files += 1
            continue
        if stats.bytes + size > budget:
            # Budget used up: stop here (later files in load order matter least) and end collection
            stats.skipped_files += 1
            break
        try:
            with open(path, 'rb', buffering=0) as file:
                while file.readinto(view):
                    if stop is not None and stop.is_set():
                        break
        except OSError:
            stats.skipped_files += 1
            continue
        stats.files += 1
        stats.bytes += size
    else:
        stats.completed = True
    stats.seconds = time.perf_counter() - started
    stats.cpu_seconds = time.thread_time() - cpu_started
    return stats


class ModPrefetcher:
    """Runs collection and prefetch on a daemon thread; at most one at a time."""

    _active: 'ModPrefetcher | None' = None
    _lock = threading.Lock()

    def __init__(self, d3dx_ini_path: Path, budget: int | None = None):
        self.d3dx_ini_path = d3dx_ini_path
        self.budget = budget
        self.stop_event = threading.Event()
        self.stats: PrefetchStats | None = None
        self.thread = threading.Thread(target=self._run, name='ModPrefetch', daemon=True)

    @classmethod
    def start(cls, d3dx_ini_path: Path, budget: int | None = None) -> 'ModPrefetcher':
        with cls._lock:
            if cls._active is not None:
                cls._active.stop()
            cls._active = prefetcher = cls(d3dx_ini_path, budget)
        prefetcher.thread.start()
        return prefetcher

    def stop(self):
        self.stop_event.set()

    def _run(self):
        try:
            _enter_background_mode()
            budget = memory_budget() if self.budget is None else self.budget
            if budget <= 0:
                log.debug('Mod prefetch skipped: no memory budget')
                return
            if self.stop_event.is_set():
                return
            paths = iter_mod_files(self.d3dx_ini_path, self.stop_event)
            self.stats = prefetch_files(paths, budget, self.stop_event)
            log.debug(f'Mod prefetch: {self.stats.files} files, {self.stats.bytes / 1e6:.1f} MB in '
                      f'{self.stats.seconds:.2f}s (skipped {self.stats.skipped_files}, completed={self.stats.completed})')
        except Exception:
            log.exception('Mod prefetch failed')
        finally:
            try:
                _leave_background_mode()
            except Exception:
                pass
