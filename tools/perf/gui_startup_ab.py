#!/usr/bin/env python3
"""Launcher start-up, base vs head, each in its own environment.

Each side gets a fresh venv with exactly the pins of its own requirements.txt, so a change that
updates a dependency is measured (and started) with the new version while the base keeps the old
one. bench_gui_startup.py then runs once per side with that side's interpreter, sources, Themes and
Locale. Prints the installed versions and both results, and appends them to $GITHUB_STEP_SUMMARY.

Usage (Windows runs the real launcher; Linux needs Tk and xvfb-run):
    python tools/perf/gui_startup_ab.py --base BASE_TREE --head . --work DIR [--runs 4] [--launches 2]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PERF = Path(__file__).resolve().parent
# Only needed to build the frozen launcher
BUILD_ONLY = re.compile(r'^nuitka\b', re.IGNORECASE)
# Packages that exist only on Windows; elsewhere the benchmark stubs them
WINDOWS_ONLY = re.compile(r'^(pywin32|wmi|winshell)\b', re.IGNORECASE)
# The Windows PyAV wheel is a direct URL; other platforms install the same version from PyPI
AV_WHEEL = re.compile(r'^av\s*@\s*\S*/av-([0-9][0-9.]*)-[^/]*\.whl\s*$', re.IGNORECASE)
REPORTED = ('pillow', 'certifi')


def requirement_lines(text: str, windows: bool) -> list[str]:
    """The tree's pins the benchmark needs on this platform."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or BUILD_ONLY.match(line):
            continue
        if not windows:
            if WINDOWS_ONLY.match(line):
                continue
            wheel = AV_WHEEL.match(line)
            if wheel:
                line = f'av=={wheel.group(1)}'
        lines.append(line)
    return lines


def venv_python(venv: Path, windows: bool) -> Path:
    return venv / ('Scripts/python.exe' if windows else 'bin/python')


def make_env(tree: Path, venv: Path, windows: bool) -> Path:
    """A venv at `venv` with the requirements of `tree`; returns its interpreter."""
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    requirements = venv / 'requirements.txt'
    text = (tree / 'requirements.txt').read_text(encoding='utf-8')
    requirements.write_text('\n'.join(requirement_lines(text, windows)) + '\n', encoding='utf-8')
    python = venv_python(venv, windows)
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(requirements)],
                   check=True)
    return python


def pinned_versions(text: str, names=REPORTED) -> dict:
    """The exact versions `text` (a requirements.txt) pins for `names`."""
    pins = {}
    for raw in text.splitlines():
        name, separator, version = raw.strip().partition('==')
        if separator and name.strip().lower() in names:
            pins[name.strip().lower()] = version.strip()
    return pins


def check_pins(installed: dict, pinned: dict, tree: Path) -> None:
    """Fail unless the environment holds exactly the tree's pins (so neither side silently runs
    the other side's versions)."""
    wrong = {name: (installed.get(name), version) for name, version in pinned.items()
             if installed.get(name) != version}
    if wrong:
        raise RuntimeError(f'environment for {tree} does not match its requirements.txt '
                           f'(installed, pinned): {wrong}')


def installed_versions(python: Path, names=REPORTED) -> dict:
    code = ('import json, importlib.metadata as m; '
            f'print(json.dumps({{n: m.version(n) for n in {list(names)!r}}}))')
    out = subprocess.run([str(python), '-c', code], check=True, capture_output=True, text=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def startup(python: Path, tree: Path, runs: int, launches: int) -> dict:
    command = [str(python), str(PERF / 'bench_gui_startup.py'), '--runs', str(runs), '--launches', str(launches),
               '--src', str(tree / 'src' / 'xxmi_launcher'), '--assets', str(tree)]
    if sys.platform != 'win32':
        command = ['xvfb-run', '-a'] + command
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'start-up benchmark failed for {tree}:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}')
    return json.loads([line for line in result.stdout.splitlines() if line.startswith('{')][-1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=Path, required=True, help='base checkout (holds requirements.txt and src/)')
    ap.add_argument('--head', type=Path, required=True, help='head checkout')
    ap.add_argument('--work', type=Path, required=True, help='folder for the two environments')
    ap.add_argument('--runs', type=int, default=4)
    ap.add_argument('--launches', type=int, default=2)
    args = ap.parse_args(argv)
    windows = sys.platform == 'win32'
    lines = ['', f'### Launcher window startup (real launcher, {args.runs} installs x {args.launches} starts, '
                 'each version in its own environment)', '']
    for side, tree in (('base', args.base.resolve()), ('this change', args.head.resolve())):
        python = make_env(tree, args.work / f'venv-{"base" if side == "base" else "head"}', windows)
        installed = installed_versions(python)
        check_pins(installed, pinned_versions((tree / 'requirements.txt').read_text(encoding='utf-8')), tree)
        versions = ', '.join(f'{name} {version}' for name, version in installed.items())
        print(f'{side}: {versions}', flush=True)
        result = startup(python, tree, args.runs, args.launches)
        lines.append(f'- {side} ({versions}): `{json.dumps(result)}`')
    summary = '\n'.join(lines) + '\n'
    print(summary)
    step_summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if step_summary:
        with open(step_summary, 'a', encoding='utf-8') as handle:
            handle.write(summary)
    return 0


if __name__ == '__main__':
    sys.exit(main())
