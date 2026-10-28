"""Run native regressions and a read-only real-asset CPU benchmark; never starts a game."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--work', type=Path, required=True)
p.add_argument('--assets', type=Path, required=True)
args = p.parse_args()
w = args.work.resolve()
env = dict(os.environ)
env.update(json.loads((w / 'build-env.json').read_text()))
native = w / 'native'
mod = native / 'DirectX11/ModLoading'
cl = w / 'toolchain/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe'
root = Path(__file__).resolve().parents[2]
common = [str(cl), '/nologo', '/O2', '/MT', '/EHsc', '/std:c++14', '/DUNICODE',
          '/D_UNICODE', '/DNOMINMAX', '/DWIN32_LEAN_AND_MEAN', '/D_CRT_SECURE_NO_WARNINGS']

def compile_program(name, sources, include, definitions=()):
    out = w / 'checks' / name
    out.mkdir(parents=True, exist_ok=True)
    exe = out / (name + '.exe')
    command = common + list(definitions) + ['/I' + str(include)] + list(map(str, sources))
    command += ['/Fe:' + str(exe), '/link', 'ole32.lib', 'windowscodecs.lib']
    result = subprocess.run(command, cwd=out, env=env, capture_output=True)
    (out / 'build.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).decode(errors='replace'))
    return exe

def run(exe, arguments=()):
    result = subprocess.run([str(exe), *map(str, arguments)], cwd=exe.parent,
                            env=env, capture_output=True, timeout=120)
    (exe.parent / 'run.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).decode(errors='replace'))
    return result.stdout.decode(errors='replace')

cpu = compile_program('cpu', [native / 'tests/preparation_cpu/main.cpp',
    native / 'tests/preparation_cpu/disk.cpp'] + [mod / (n + '.cpp') for n in
    ['SnapshotIO', 'CpuAssetCache', 'DiskAssetCache', 'AssetCatalog', 'PreparationScheduler']], mod)
print(run(cpu), flush=True)
old = w / 'baseline'
old.mkdir(exist_ok=True)
with zipfile.ZipFile(root / 'development-builds/prepared-loading/XXMI-Prepared-Loading-development-win-x64.zip') as archive:
    prefix = 'benchmark-baseline/' if any(n.startswith('benchmark-baseline/') for n in archive.namelist()) else 'native/DirectX11/ModLoading/'
    for item in archive.infolist():
        if item.filename.startswith(prefix) and not item.is_dir():
            (old / Path(item.filename).name).write_bytes(archive.read(item))
programs = {}
for label, folder in [('before', old), ('after', mod)]:
    programs[label] = compile_program('bench-' + label,
        [root / 'tools/PreparedLoading/benchmark-cpu.cpp'] + [folder / (n + '.cpp') for n in
        ['SnapshotIO', 'CpuAssetCache', 'DiskAssetCache', 'PreparationScheduler']], folder,
        ['/DLEGACY_SCHEDULER'] if label == 'before' else [])
# Deterministic sample: twelve largest ordinary buffer files that fit the 4 MiB
# per-asset ceiling. No selected source is copied, modified or executed.
assets = args.assets.resolve()
paths = sorted((f for f in assets.rglob('*.buf') if f.is_file() and
                not f.stat().st_file_attributes & 1024 and f.stat().st_nlink == 1 and
                1024 * 1024 <= f.stat().st_size <= 4 * 1024 * 1024),
               key=lambda f: (-f.stat().st_size, str(f)))[:12]
if len(paths) != 12:
    raise RuntimeError('Need twelve matching real buffer assets for this benchmark')
identity = [{'path': str(f.relative_to(assets)), 'bytes': f.stat().st_size,
             'sha256': hashlib.sha256(f.read_bytes()).hexdigest()} for f in paths]
results = []
for trial in range(3):
    # Alternate order to reduce warm-file-cache/order bias. No OS cache purge.
    order = ['before', 'after-unbounded', 'after'] if trial % 2 == 0 else ['after', 'after-unbounded', 'before']
    for label in order:
        result = json.loads(run(programs['before' if label == 'before' else 'after'], ['bounded' if label == 'after' else 'legacy', *paths]))
        result.update(candidate=label, trial=trial + 1)
        results.append(result)
        print(json.dumps(result), flush=True)
receipt = {'scope': 'CPU snapshot/cache only; warm OS cache; excludes GPU, game startup, F10 and frames',
           'assets': identity, 'results': results, 'game_launched': False,
           'uac_requested': False}
(w / 'speed-results.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
