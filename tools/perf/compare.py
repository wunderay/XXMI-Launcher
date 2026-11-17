#!/usr/bin/env python3
"""Mandatory before/after performance comparison of two launcher source trees.

Runs the tools/perf benchmarks (this checkout's versions) against BASE and HEAD sources on the
same machine, prints a Markdown table (also appended to $GITHUB_STEP_SUMMARY) and exits 1 if
HEAD regresses any metric beyond its tolerance. Deterministic metrics (file-system syscalls,
image resizes, update-check traffic) get a tight tolerance; timings and memory get a relative
tolerance plus an absolute noise floor.

Usage (Linux; needs strace, xvfb-run and a Python with Tk and the GUI packages):
    python tools/perf/compare.py --base BASE/src/xxmi_launcher --head src/xxmi_launcher
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PERF = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Metric:
    name: str
    unit: str
    rel: float      # allowed relative increase
    floor: float    # increases up to this absolute amount are always noise


METRICS = {
    # Best of N for these short timings (their syscall counts below are exact)
    'prelaunch_repeat_ms': Metric('Pre-launch mod check, repeat launch (best of N)', 'ms', 0.25, 8.0),
    'prelaunch_first_ms': Metric('Pre-launch mod check, first launch (best of N)', 'ms', 0.25, 15.0),
    'prelaunch_repeat_syscalls': Metric('Pre-launch file syscalls, repeat', '', 0.01, 20),
    'prelaunch_first_syscalls': Metric('Pre-launch file syscalls, first', '', 0.01, 20),
    'game_load_cold_s': Metric('Cold-cache mod load by the DLL (simulated, 2 s boot)', 's', 0.25, 0.05),
    'gui_open_first_s': Metric('Window open, first start', 's', 0.15, 0.08),
    'gui_open_repeat_s': Metric('Window open, repeat start', 's', 0.15, 0.08),
    'gui_cpu_repeat_s': Metric('Startup CPU, repeat start', 's', 0.15, 0.08),
    'gui_peak_rss_first_mb': Metric('Peak memory, first start', 'MB', 0.05, 5.0),
    'gui_peak_rss_repeat_mb': Metric('Peak memory, repeat start', 'MB', 0.05, 5.0),
    'gui_rss_repeat_mb': Metric('Memory after startup, repeat start', 'MB', 0.05, 5.0),
    'gui_image_resizes_repeat': Metric('Image resizes, repeat start', '', 0.0, 2),
    'update_bytes': Metric('Update checks: bytes for 3 checks (both channels)', 'B', 0.01, 64),
    'update_counted_requests': Metric('Update checks: rate-limited requests', '', 0.0, 0),
}


def run_json(command, env=None, xvfb=False):
    if xvfb:
        command = ['xvfb-run', '-a'] + command
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f'{" ".join(map(str, command))} failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}')
    lines = [line for line in result.stdout.splitlines() if line.startswith('{')]
    return json.loads(lines[-1])


def measure(src: Path, mods: Path, game_mods: Path, runs: int) -> dict:
    env = {**os.environ, 'PYTHONPATH': str(src)}
    python = sys.executable
    values = {}

    prelaunch = run_json([python, PERF / 'bench_prelaunch.py', mods / 'ZZMI', '--runs', str(runs), '--syscalls'], env)
    values['prelaunch_repeat_ms'] = prelaunch['repeat']['min_ms']
    values['prelaunch_first_ms'] = prelaunch['first']['min_ms']
    values['prelaunch_repeat_syscalls'] = prelaunch['repeat']['fs_syscalls']
    values['prelaunch_first_syscalls'] = prelaunch['first']['fs_syscalls']

    # Each version as it behaves: with the launcher's prefetch where the version has one
    game = [python, PERF / 'simulate_game_load.py', game_mods / 'ZZMI', '--cold', '--boot-delay', '2', '--repeat', '5']
    if (src / 'core' / 'mod_prefetch.py').is_file():
        game.append('--prefetch')
    values['game_load_cold_s'] = run_json(game, env)['median_load_s']

    gui = run_json([python, PERF / 'bench_gui_startup.py', '--runs', str(max(3, runs // 2)), '--launches', '2',
                    '--src', src], xvfb=True)
    values['gui_open_first_s'] = gui['first_start']['gui_open_s']
    values['gui_open_repeat_s'] = gui['later_starts']['gui_open_s']
    values['gui_cpu_repeat_s'] = gui['later_starts']['cpu_s']
    values['gui_peak_rss_first_mb'] = gui['first_start']['max_rss_mb']
    values['gui_peak_rss_repeat_mb'] = gui['later_starts']['max_rss_mb']
    values['gui_rss_repeat_mb'] = gui['later_starts']['rss_mb']
    values['gui_image_resizes_repeat'] = gui['later_starts']['image_resize_calls']

    update = run_json([python, PERF / 'bench_update_check.py'], env)
    values['update_bytes'] = sum(channel['body_bytes'] for channel in update.values())
    values['update_counted_requests'] = sum(channel['counted_requests'] for channel in update.values())
    return values


def regressed(metric: Metric, base: float, head: float) -> bool:
    increase = head - base
    return increase > metric.floor and increase > base * metric.rel


def report(base: dict, head: dict) -> tuple[str, list[str]]:
    lines = ['## Performance comparison (base → head, same runner)', '',
             '| Metric | Base | Head | Change | Result |', '| --- | ---: | ---: | ---: | --- |']
    failures = []
    for key, metric in METRICS.items():
        b, h = base[key], head[key]
        change = f'{(h - b) / b * 100:+.1f}%' if b else ('0' if h == b else 'new')
        bad = regressed(metric, b, h)
        if bad:
            failures.append(metric.name)
        unit = f' {metric.unit}' if metric.unit else ''
        lines.append(f'| {metric.name} | {b:g}{unit} | {h:g}{unit} | {change} | '
                     f'{"❌ regression" if bad else "✅"} |')
    lines += ['', 'A metric fails when it grows by more than its relative tolerance *and* its noise floor '
                  '(timings 15–25%, memory 5% / 5 MB, counts ~1%).']
    if failures:
        lines += ['', f'**{len(failures)} regression(s):** ' + ', '.join(failures)]
    return '\n'.join(lines) + '\n', failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=Path, required=True, help='base src/xxmi_launcher')
    ap.add_argument('--head', type=Path, required=True, help='head src/xxmi_launcher')
    ap.add_argument('--runs', type=int, default=7)
    ap.add_argument('--json', type=Path, help='also write raw values here')
    args = ap.parse_args()
    for tool in ('strace', 'xvfb-run'):
        if shutil.which(tool) is None:
            sys.exit(f'{tool} is required')
    with tempfile.TemporaryDirectory(prefix='xxmi-perf-compare-') as work:
        mods, game_mods = Path(work) / 'mods', Path(work) / 'game'
        subprocess.run([sys.executable, PERF / 'make_mods.py', mods, '--mods', '1000', '--scale', '0.02', '--seed', '2'],
                       check=True, capture_output=True)
        subprocess.run([sys.executable, PERF / 'make_mods.py', game_mods, '--mods', '200', '--scale', '0.25', '--seed', '3'],
                       check=True, capture_output=True)
        # Same generated mod sets and machine for both versions
        base = measure(args.base.resolve(), mods, game_mods, args.runs)
        head = measure(args.head.resolve(), mods, game_mods, args.runs)
    if args.json:
        args.json.write_text(json.dumps({'base': base, 'head': head}, indent=2), encoding='utf-8')
    summary, failures = report(base, head)
    print(summary)
    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a', encoding='utf-8') as handle:
            handle.write(summary)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
