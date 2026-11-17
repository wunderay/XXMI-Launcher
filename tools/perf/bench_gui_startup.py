#!/usr/bin/env python3
"""Measure launcher GUI startup: seconds until the main window opens, CPU seconds, peak memory
(RSS, or peak working set on Windows) and image work. On Windows it runs the real launcher; on
Linux Windows-only modules are stubbed so it runs headless (Xvfb).

The launcher is Windows-only, so absolute numbers differ from Windows; use it for A/B
comparisons of the same machine and Python. Excludes the network update check.

Needs a Python with Tk plus the GUI packages from requirements.txt (except pywin32/WMI/winshell):
    uv python install 3.12 && uv venv -p 3.12 --managed-python .venv-perf
    VIRTUAL_ENV=.venv-perf uv pip install customtkinter pillow av tkinterweb markdown py-gfm \\
        dacite requests cryptography psutil packaging pefile vdf zstandard mslex attrs cattrs darkdetect
Usage (runs a throwaway copy, never the checkout):
    xvfb-run -a .venv-perf/bin/python tools/perf/bench_gui_startup.py [--runs 7] [--src src/xxmi_launcher]
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

CHILD = r'''
import sys, os, time, json, threading, runpy, ctypes, importlib.abc, importlib.machinery
if sys.platform != 'win32':
    import resource
from unittest import mock
from pathlib import Path
T0 = time.perf_counter()
app_dir = sys.argv[1]
sys.path.insert(0, app_dir); os.chdir(app_dir)

# --- Windows stubs (other platforms only) -----------------------------------------------------
WINDOWS = sys.platform == 'win32'
STUBBED = {'win32api', 'win32gui', 'win32process', 'win32crypt', 'win32con', 'pywintypes', 'winshell',
           'wmi', 'msvcrt', 'pythoncom'}
class Stubs(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name in STUBBED or name.split('.')[0] == 'win32com':
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
    def create_module(self, spec):
        module = mock.MagicMock(name=spec.name); module.__spec__ = spec; module.__path__ = []
        module.__name__ = spec.name; return module
    def exec_module(self, module): pass
if not WINDOWS:
    sys.meta_path.insert(0, Stubs())
import types
winreg = types.ModuleType('winreg')
def _missing(*a, **k): raise FileNotFoundError('registry stub')
for _n in ('OpenKey', 'OpenKeyEx', 'QueryValueEx', 'EnumKey', 'EnumValue', 'CreateKey', 'CreateKeyEx',
           'SetValueEx', 'DeleteKey', 'DeleteValue', 'ConnectRegistry'):
    setattr(winreg, _n, _missing)
winreg.CloseKey = lambda *a: None
winreg.HKEYType = type('HKEYType', (), {})
winreg.__getattr__ = lambda name: 0
def _windll(name, *a, **k):
    library = mock.MagicMock(); library._handle = 1; return library
if not WINDOWS:
    sys.modules['winreg'] = winreg
    ctypes.WinDLL = _windll
    ctypes.windll = mock.MagicMock(); ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    ctypes.windll.user32.GetSystemMetrics.side_effect = lambda i: 1920 if i == 0 else 1080
    ctypes.windll.user32.GetParent.return_value = 0
    ctypes.windll.user32.GetWindowLongW.return_value = 0
    os.getlogin = lambda: 'bench'
    import tkinter
    for _name in ('attributes', 'iconbitmap', 'iconphoto'):
        _orig = getattr(tkinter.Wm, _name)
        def _tolerant(self, *a, _orig=_orig, **k):
            try: return _orig(self, *a, **k)
            except tkinter.TclError: return None
        setattr(tkinter.Wm, _name, _tolerant); setattr(tkinter.Wm, 'wm_' + _name, _tolerant)

# --- Launcher setup in the order app.py uses ----------------------------------------------------
import core.locale_manager as Locale
import core.path_manager as Paths
Locale.initialize(Path(app_dir).parent.parent)
Paths.initialize(Path(app_dir).parent.parent)
Locale.initialize = Paths.initialize = lambda root: None
if not WINDOWS:
    Paths.is_read_only = lambda path: False   # NTFS attribute check
    import gui.classes.windows as W
    W.get_monitor_dpi = lambda: (96, 96)

# --- Image work counters ------------------------------------------------------------------------
from PIL import Image
counts = {'image_resize_calls': 0}
_resize = Image.Image.resize
def _counted_resize(self, *a, **k):
    counts['image_resize_calls'] += 1; return _resize(self, *a, **k)
Image.Image.resize = _counted_resize

import core.application as A
A.Application.auto_update = lambda self, *a, **k: None   # network, excluded
import gui.windows.main.main_window as MW
def bench_open(self, *a, **k):
    marks = {'gui_open_s': round(time.perf_counter() - T0, 3)}
    def done():
        if WINDOWS:
            import psutil
            process = psutil.Process()
            times = process.cpu_times()
            cpu, peak = times.user + times.system, process.memory_info().peak_wset
        else:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            cpu, peak = usage.ru_utime + usage.ru_stime, usage.ru_maxrss * 1024
        import psutil
        current = psutil.Process().memory_info().rss
        marks.update(cpu_s=round(cpu, 3), max_rss_mb=round(peak / 1024 / 1024, 1), rss_mb=round(current / 1024 / 1024, 1),
                     **counts, av_loaded='av' in sys.modules, pyglet_loaded='pyglet' in sys.modules)
        print('BENCH ' + json.dumps(marks), flush=True)
        os._exit(0)
    self.after(1000, done)
    self.mainloop()
MW.MainWindow.open = bench_open
sys.argv = [os.path.join(app_dir, 'app.py')]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def run_once(src: Path, launches: int = 1, assets: Path = REPO) -> list[dict]:
    """Start the launcher `launches` times in one fresh installation (later starts reuse caches)."""
    with tempfile.TemporaryDirectory(prefix='xxmi-gui-bench-') as work:
        root = Path(work)
        shutil.copytree(src, root / 'src' / 'xxmi_launcher', ignore=shutil.ignore_patterns('__pycache__'))
        for folder in ('Locale', 'Themes'):
            shutil.copytree(assets / folder, root / folder)
        app = root / 'src' / 'xxmi_launcher'
        # Compile once so every run measures warm bytecode, as an installed launcher would
        subprocess.run([sys.executable, '-m', 'compileall', '-q', str(app)], check=True, capture_output=True)
        results = []
        for _ in range(launches):
            out = subprocess.run([sys.executable, '-c', CHILD, str(app)], capture_output=True, text=True, timeout=120,
                                 env={**os.environ, 'HTTPS_PROXY': 'http://127.0.0.1:9', 'HTTP_PROXY': 'http://127.0.0.1:9'})
            line = next((l for l in out.stdout.splitlines() if l.startswith('BENCH ')), None)
            if line is None:
                raise RuntimeError(f'benchmark run failed:\n{out.stdout[-2000:]}\n{out.stderr[-4000:]}')
            results.append(json.loads(line[6:]))
            time.sleep(0.5)  # let background cache writes finish, as between real launches
        return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=7)
    ap.add_argument('--src', type=Path, default=REPO / 'src' / 'xxmi_launcher')
    ap.add_argument('--launches', type=int, default=1, help='starts per installation; reports each start separately')
    ap.add_argument('--assets', type=Path, default=REPO, help='folder holding the Locale and Themes to start with')
    args = ap.parse_args()
    series = [run_once(args.src.resolve(), args.launches, args.assets.resolve()) for _ in range(args.runs)]

    def summarize(runs):
        summary = {key: statistics.median(run[key] for run in runs) for key in runs[0] if not isinstance(runs[0][key], bool)}
        summary.update({key: runs[0][key] for key in runs[0] if isinstance(runs[0][key], bool)})
        summary['runs'] = len(runs)
        return summary

    if args.launches == 1:
        print(json.dumps(summarize([launches[0] for launches in series])))
    else:
        print(json.dumps({'first_start': summarize([launches[0] for launches in series]),
                          'later_starts': summarize([run for launches in series for run in launches[1:]])}))


if __name__ == '__main__':
    main()
