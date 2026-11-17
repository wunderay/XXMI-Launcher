#!/usr/bin/env python3
"""Benchmark the launcher's pre-launch Mods/ShaderFixes optimizer (runs synchronously on every
game start) against a folder made by make_mods.py.

Runs the real core.mod_manager code with configuration/GUI dependencies stubbed. Measures:
  first  - empty optimizer cache (first launch, or after the user resets the cache)
  repeat - cache already populated (every later launch)
Reports median wall time, plus (with --syscalls, Linux + strace) the exact number of file-system
system calls one run makes. Each such call costs far more on Windows/NTFS with antivirus than
on Linux, so the count is the portable signal; wall time here is a lower bound.

Usage: PYTHONPATH=src/xxmi_launcher python3 tools/perf/bench_prelaunch.py <root>/ZZMI [--runs N]
"""
import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

# The real config manager pulls in Windows-only packages; mod_manager only reads two fields.
Config = SimpleNamespace(Launcher=SimpleNamespace(active_importer="ZZMI"), Active=SimpleNamespace(Importer=None))
sys.modules["core.config_manager"] = Config
import core.event_manager as Events  # noqa: E402
from core import mod_manager  # noqa: E402

def run_once(importer: Path, cache: Path, reset: bool):
    manager = mod_manager.ModManager()
    excludes = ["DISABLED*"]
    t0 = time.perf_counter()
    manager.optimize_mods_folder(mods_path=importer / "Mods", cache_path=cache, dry_run=True,
                                 use_cache=True, reset_cache=reset, exclude_patterns=excludes)
    manager.optimize_shaderfixes_folder(shaderfixes_path=importer / "ShaderFixes", dry_run=True,
                                        exclude_patterns=excludes)
    return time.perf_counter() - t0


FS_SYSCALLS = "trace=%file,%desc,getdents64"


def count_syscalls(importer: Path, phase: str) -> int:
    """Syscalls of one run in a child process, minus the same child doing no run."""
    def traced(do_run: str) -> int:
        out = subprocess.run(["strace", "-f", "-c", "-e", FS_SYSCALLS, sys.executable, __file__, str(importer),
                              "--child", phase, "--child-run", do_run], capture_output=True, text=True, check=True)
        total = [line for line in out.stderr.splitlines() if line.strip().endswith("total")]
        return int(total[-1].split()[3]) if total else 0
    return traced("1") - traced("0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("importer", type=Path)
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--syscalls", action="store_true", help="also count file-system syscalls with strace")
    ap.add_argument("--child", choices=["first", "repeat"], help=argparse.SUPPRESS)
    ap.add_argument("--child-run", default="1", help=argparse.SUPPRESS)
    args = ap.parse_args()
    importer = args.importer.resolve()
    work = Path(tempfile.mkdtemp(prefix="xxmi-bench-"))
    cache = work / "ZZMI.json"
    fake_importer = SimpleNamespace(importer_path=importer)
    # Answer every notification with "Ignore" so the run never blocks or edits mods.
    Config.Active.Importer = fake_importer
    if args.child:
        with mock.patch.object(Events, "Call", lambda *a, **k: False):
            if args.child == "repeat":
                run_once(importer, cache, False)  # populate the cache (untraced work is subtracted below)
            if args.child_run == "1":
                run_once(importer, cache, args.child == "first")
            elif args.child == "repeat":
                pass
        shutil.rmtree(work, ignore_errors=True)
        return
    with mock.patch.object(Events, "Call", lambda *a, **k: False):
        results = {}
        for label, reset in (("first", True), ("repeat", False)):
            times = []
            for _ in range(args.runs):
                if reset and cache.exists():
                    cache.unlink()
                if not reset and not cache.exists():
                    run_once(importer, cache, False)
                times.append(run_once(importer, cache, reset))
            results[label] = {"median_ms": round(statistics.median(times) * 1000, 1),
                              "min_ms": round(min(times) * 1000, 1)}
            if args.syscalls:
                results[label]["fs_syscalls"] = count_syscalls(importer, label)
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(results))


if __name__ == "__main__":
    main()
