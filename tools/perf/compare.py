#!/usr/bin/env python3
"""Mandatory before/after performance comparison of two launcher source trees.

Runs the tools/perf benchmarks (this checkout's versions) against BASE and HEAD sources on the
same machine in interleaved rounds (base/head, then head/base, ...) after an unmeasured warm-up of
each, keeping each metric's best round so neither side gains from running second, prints a Markdown table (also appended to $GITHUB_STEP_SUMMARY) and exits 1 if
HEAD regresses any metric beyond its tolerance. Deterministic metrics (file-system syscalls,
image resizes, update-check traffic) get a tight tolerance; timings and memory get a relative
tolerance plus an absolute noise floor.

Usage (Linux; needs strace, xvfb-run and Pythons with Tk and the GUI packages):
    python tools/perf/compare.py --base BASE/src/xxmi_launcher --head src/xxmi_launcher \
        [--base-python BASE_VENV/bin/python --head-python HEAD_VENV/bin/python]
Give each version its own interpreter when their requirements differ (a dependency update);
otherwise both run with this interpreter.
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
    # Responses other than an empty 304 (the mock counts every 304 as free; GitHub waives the
    # charge for a 304 only on token-authenticated requests)
    'update_counted_requests': Metric('Update checks: non-304 responses', '', 0.0, 0),
}


def run_json(command, env=None, xvfb=False):
    if xvfb:
        command = ['xvfb-run', '-a'] + command
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f'{" ".join(map(str, command))} failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}')
    lines = [line for line in result.stdout.splitlines() if line.startswith('{')]
    return json.loads(lines[-1])


def measure(src: Path, mods: Path, game_mods: Path, runs: int, python=None) -> dict:
    """One round of every benchmark for the launcher at `src`, run with `python` (an interpreter
    with that version's own dependencies; this one by default)."""
    env = {**os.environ, 'PYTHONPATH': str(src)}
    python = str(python or sys.executable)
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

    # Each version starts with its own Themes and Locale (from the tree that holds `src`)
    gui = run_json([python, PERF / 'bench_gui_startup.py', '--runs', str(max(3, runs // 2)), '--launches', '2',
                    '--src', src, '--assets', src.parents[1]], xvfb=True)
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


def best_of(rounds: list[dict]) -> dict:
    """Each metric's lowest value over the rounds (lower is better for every metric)."""
    return {key: min(values[key] for values in rounds) for key in rounds[0]}


def measure_interleaved(base_src: Path, head_src: Path, mods: Path, game_mods: Path, runs: int, rounds: int,
                        measure_fn=None, base_python=None, head_python=None) -> tuple[dict, dict]:
    measure_fn = measure_fn or measure
    pythons = {base_src: base_python, head_src: head_python}
    # Warm-up: the first process to import a fresh tree pays cold-disk costs; measure neither
    for src in (base_src, head_src):
        measure_fn(src, mods, game_mods, 1, pythons[src])
    results = {base_src: [], head_src: []}
    for index in range(rounds):
        order = (base_src, head_src) if index % 2 == 0 else (head_src, base_src)
        for src in order:
            results[src].append(measure_fn(src, mods, game_mods, runs, pythons[src]))
    return best_of(results[base_src]), best_of(results[head_src])


def regressed(metric: Metric, base: float, head: float) -> bool:
    increase = head - base
    return increase > metric.floor and increase > base * metric.rel


def report(base: dict, head: dict) -> tuple[str, list[str]]:
    lines = ['## Performance comparison (base → head, same runner, best of interleaved rounds)', '',
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
    ap.add_argument('--rounds', type=int, default=4, help='interleaved measurement rounds per version')
    ap.add_argument('--base-python', type=Path, help="interpreter with base's dependencies (default: this one)")
    ap.add_argument('--head-python', type=Path, help="interpreter with head's dependencies (default: this one)")
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
        base, head = measure_interleaved(args.base.resolve(), args.head.resolve(), mods, game_mods,
                                         args.runs, args.rounds, base_python=args.base_python,
                                         head_python=args.head_python)
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
