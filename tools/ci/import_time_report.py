"""Record import times of launcher core modules that are importable on this host.

Each module is imported in a fresh interpreter with ``-X importtime`` several
times after one warm-up run (so bytecode compilation is excluded) and the
median cumulative time is reported. Modules that need a known Windows-only package
are listed as platform exclusions; any other import error is reported as an
unexpected failure and fails the run. Timings are informational only.

Usage: python tools/ci/import_time_report.py --output-dir DIR [--runs N]
"""
import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'src' / 'xxmi_launcher'

# Packages that exist only on Windows (pywin32, WMI, winshell, stdlib Windows modules)
WINDOWS_ONLY = {'win32api', 'win32gui', 'win32process', 'win32crypt', 'win32con', 'win32com', 'pywintypes',
                'pythoncom', 'winreg', 'msvcrt', '_winapi', 'winshell', 'wmi'}
MISSING_MODULE = re.compile(r"^ModuleNotFoundError: No module named '([^'.]+)")


# Windows-only ctypes features used at import time
WINDOWS_ONLY_CTYPES = re.compile(r"^AttributeError: module 'ctypes' has no attribute '(WinDLL|windll|WINFUNCTYPE|WinError)'")


def is_platform_exclusion(error: str) -> bool:
    match = MISSING_MODULE.match(error or '')
    if match:
        return match.group(1) in WINDOWS_ONLY
    return bool(WINDOWS_ONLY_CTYPES.match(error or ''))


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

    measured, skipped, failed = [], [], []
    for module in discover():
        _, error, _ = run_once(module, env)  # warm-up and importability probe
        if error:
            (skipped if is_platform_exclusion(error) else failed).append({'module': module, 'reason': error})
            continue
        samples, raw, errors = [], '', []
        for _ in range(args.runs):
            value, error, raw = run_once(module, env)
            if value is not None:
                samples.append(value)
            else:
                errors.append(error or 'no importtime line')
        if not samples:
            failed.append({'module': module, 'reason': errors[-1]})
            continue
        (args.output_dir / f'{module}.importtime.txt').write_text(raw, encoding='utf-8')
        measured.append({
            'module': module,
            'median_us': int(statistics.median(samples)),
            'min_us': min(samples),
            'max_us': max(samples),
            'samples_us': samples,
            'missing_samples': args.runs - len(samples),
        })

    measured.sort(key=lambda item: item['median_us'], reverse=True)
    report = {
        'python': sys.version,
        'platform': platform.platform(),
        'runs': args.runs,
        'measured': measured,
        'skipped': skipped,
        'failed': failed,
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
        partial = f" ({item['missing_samples']} of {args.runs} samples missing)" if item['missing_samples'] else ''
        lines.append(f"| `{item['module']}`{partial} | {item['median_us'] / 1000:.1f} | "
                     f"{item['min_us'] / 1000:.1f} | {item['max_us'] / 1000:.1f} |")
    if failed:
        lines += ['', f'**{len(failed)} module(s) failed to import unexpectedly (not measured):**', '']
        lines += [f"- `{item['module']}`: {item['reason']}" for item in failed]
    if skipped:
        lines += ['', f'{len(skipped)} module(s) excluded on this platform (Windows-only dependencies):', '']
        lines += [f"- `{item['module']}`: {item['reason']}" for item in skipped]
    summary = '\n'.join(lines) + '\n'
    (args.output_dir / 'summary.md').write_text(summary, encoding='utf-8')
    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a', encoding='utf-8') as handle:
            handle.write(summary)
    print(summary)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
