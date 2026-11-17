"""Record import times of launcher core modules that are importable on this host.

Each module is imported in a fresh interpreter with ``-X importtime`` several
times after one warm-up run (so bytecode compilation is excluded) and the
median cumulative time is reported. Modules that need Windows-only packages are
listed as skipped. The script never fails on timings; it only reports them.

Usage: python tools/ci/import_time_report.py --output-dir DIR [--runs N]
"""
import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'src' / 'xxmi_launcher'


def discover(package='core'):
    base = SOURCE / package
    modules = []
    for path in sorted(base.rglob('*.py')):
        parts = path.relative_to(SOURCE).with_suffix('').parts
        if parts[-1] == '__init__':
            parts = parts[:-1]
        modules.append('.'.join(parts))
    return modules


def run_once(module, env):
    result = subprocess.run(
        [sys.executable, '-X', 'importtime', '-c', f'import {module}'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        last = (result.stderr.strip().splitlines() or ['import failed'])[-1]
        return None, last, result.stderr
    cumulative = None
    for line in result.stderr.splitlines():
        if not line.startswith('import time:') or '|' not in line:
            continue
        fields = [field.strip() for field in line[len('import time:'):].split('|')]
        if len(fields) == 3 and fields[2].strip() == module and fields[1].isdigit():
            cumulative = int(fields[1])
    return cumulative, None, result.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--runs', type=int, default=5)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(SOURCE), env.get('PYTHONPATH')]))

    measured, skipped = [], []
    for module in discover():
        _, error, _ = run_once(module, env)  # warm-up and importability probe
        if error:
            skipped.append({'module': module, 'reason': error})
            continue
        samples, raw = [], ''
        for _ in range(args.runs):
            value, error, raw = run_once(module, env)
            if value is not None:
                samples.append(value)
        if not samples:
            skipped.append({'module': module, 'reason': error or 'no importtime line'})
            continue
        (args.output_dir / f'{module}.importtime.txt').write_text(raw, encoding='utf-8')
        measured.append({
            'module': module,
            'median_us': int(statistics.median(samples)),
            'min_us': min(samples),
            'max_us': max(samples),
            'samples_us': samples,
        })

    measured.sort(key=lambda item: item['median_us'], reverse=True)
    report = {
        'python': sys.version,
        'platform': platform.platform(),
        'runs': args.runs,
        'measured': measured,
        'skipped': skipped,
    }
    (args.output_dir / 'import-times.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    lines = [
        '## Core module import times',
        '',
        f'Python {platform.python_version()} on {platform.system()}, median of {args.runs} warm runs. '
        'Informational only; there are no thresholds.',
        '',
        '| Module | Median (ms) | Min (ms) | Max (ms) |',
        '| --- | ---: | ---: | ---: |',
    ]
    for item in measured:
        lines.append(f"| `{item['module']}` | {item['median_us'] / 1000:.1f} | "
                     f"{item['min_us'] / 1000:.1f} | {item['max_us'] / 1000:.1f} |")
    if skipped:
        lines += ['', f'{len(skipped)} module(s) not importable on this host (Windows-only dependencies):', '']
        lines += [f"- `{item['module']}`: {item['reason']}" for item in skipped]
    summary = '\n'.join(lines) + '\n'
    (args.output_dir / 'summary.md').write_text(summary, encoding='utf-8')
    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a', encoding='utf-8') as handle:
            handle.write(summary)
    print(summary)


if __name__ == '__main__':
    main()
