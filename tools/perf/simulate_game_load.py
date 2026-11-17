#!/usr/bin/env python3
"""Simulate what the 3DMigoto/XXMI DLL does with Mods when the game starts.

Model (matching 3DMigoto's [Include] handling): read d3dx.ini, walk every include_recursive
folder, skip folders and files matching exclude_recursive, parse every remaining *.ini, and read
every file referenced by a `filename =` option (buffers, index buffers, textures are loaded at
startup). Reports counts, bytes and wall time. With --cold, evicts the mod files from the OS page
cache first (posix_fadvise DONTNEED), so disk reads are part of the measurement.
"""
import argparse
import fnmatch
import json
import os
import time
from pathlib import Path


def includes(d3dx: Path):
    inc, exc, section = [], [], None
    for raw in d3dx.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line.strip("[]").lower()
        elif section == "include" and "=" in line:
            key, value = (x.strip() for x in line.split("=", 1))
            if key.lower() == "include_recursive":
                inc.append(value)
            elif key.lower() == "exclude_recursive":
                exc.append(value)
    return inc, exc


def excluded(name, patterns):
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def evict(root: Path):
    if not hasattr(os, "posix_fadvise"):
        return False
    for folder, _, files in os.walk(root):
        for f in files:
            fd = os.open(os.path.join(folder, f), os.O_RDONLY)
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(fd)
    return True


def load(importer: Path):
    inc, exc = includes(importer / "d3dx.ini")
    stats = {"ini_files": 0, "sections": 0, "resources": 0, "resource_bytes": 0, "missing": 0}
    t0 = time.perf_counter()
    for rel in inc:
        for folder, dirs, files in os.walk(importer / rel):
            dirs[:] = sorted(d for d in dirs if not excluded(d, exc))
            for f in sorted(files):
                if not f.lower().endswith(".ini") or excluded(f, exc):
                    continue
                stats["ini_files"] += 1
                path = Path(folder) / f
                for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = raw.strip()
                    if line.startswith("["):
                        stats["sections"] += 1
                    elif line.lower().startswith("filename") and "=" in line:
                        target = Path(folder) / line.split("=", 1)[1].strip()
                        try:
                            with open(target, "rb") as res:
                                while chunk := res.read(1 << 20):
                                    stats["resource_bytes"] += len(chunk)
                            stats["resources"] += 1
                        except OSError:
                            stats["missing"] += 1
    stats["load_s"] = round(time.perf_counter() - t0, 4)
    stats["resource_mb"] = round(stats.pop("resource_bytes") / 1e6, 1)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("importer", type=Path, help="e.g. <root>/ZZMI")
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--boot-delay", type=float, default=0.0,
                    help="seconds between game start and the DLL loading mods")
    ap.add_argument("--prefetch", action="store_true",
                    help="run the launcher's core.mod_prefetch during the boot delay (needs PYTHONPATH=src/xxmi_launcher)")
    args = ap.parse_args()
    runs = []
    for _ in range(args.repeat):
        if args.cold:
            evict(args.importer)
        prefetcher = None
        if args.prefetch:
            from core.mod_prefetch import ModPrefetcher
            prefetcher = ModPrefetcher.start(args.importer / "d3dx.ini")
        time.sleep(args.boot_delay)
        run = load(args.importer)
        if prefetcher:
            prefetcher.thread.join()
            stats = prefetcher.stats
            run["prefetch"] = {"files": stats.files, "mb": round(stats.bytes / 1e6, 1), "s": round(stats.seconds, 3),
                               "completed": stats.completed, "thread_cpu_s": round(stats.cpu_seconds, 3)}
        runs.append(run)
    import resource
    best = min(runs, key=lambda r: r["load_s"])
    best["max_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    best["median_load_s"] = sorted(r["load_s"] for r in runs)[len(runs) // 2]
    best["mode"] = "cold" if args.cold else "warm"
    print(json.dumps(best))


if __name__ == "__main__":
    main()
