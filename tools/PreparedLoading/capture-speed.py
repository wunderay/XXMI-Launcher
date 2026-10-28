"""Passive A/B observer. Started by the user-opened launcher; never launches a game.

Uses the loaded DLL's native trace for durations and Present intervals, without
ETW, services, drivers, installation or another elevation request.
"""
import argparse
import csv
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import statistics
import time


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def inspect_dll(pid, expected, expected_hash):
    class Module(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('id', wintypes.DWORD),
            ('pid', wintypes.DWORD), ('global_usage', wintypes.DWORD),
            ('process_usage', wintypes.DWORD), ('base', ctypes.c_void_p),
            ('bytes', wintypes.DWORD), ('module', wintypes.HMODULE),
            ('name', wintypes.WCHAR * 256), ('path', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Module32FirstW.argtypes = kernel.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Module)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(0x18, pid)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = Module(); entry.size = ctypes.sizeof(entry)
        present = kernel.Module32FirstW(handle, ctypes.byref(entry))
        observed = []
        while present:
            if entry.name.lower() == 'd3d11.dll':
                path = Path(entry.path)
                identity = {'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
                observed.append(identity)
                if path.resolve() == expected.resolve() and identity['sha256'] == expected_hash:
                    return {'verified': True, 'pid': pid, **identity}
            present = kernel.Module32NextW(handle, ctypes.byref(entry))
        return {'verified': False, 'pid': pid, 'observed': observed}
    finally:
        kernel.CloseHandle(handle)


def fingerprint(profile):
    """Cheap metadata identity, plus INI content hashes; never read all mod buffers."""
    entries = []
    for name in ['Mods', 'ShaderFixes', 'Core']:
        base = profile / 'ZZMI' / name
        for folder, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_junction())
            for name in sorted(files):
                path = Path(folder) / name
                stat = path.stat()
                if stat.st_file_attributes & 1024:
                    raise ValueError('Linked mod files cannot qualify a matched comparison')
                entries.append([str(path.relative_to(profile)), stat.st_size, stat.st_mtime_ns,
                                digest(path) if path.suffix.lower() == '.ini' else None])
    # Only the explicitly controlled preparation switches differ between presets.
    ini = (profile / 'ZZMI/d3dx.ini').read_text(encoding='utf-8-sig')
    section = ''; common = []
    for raw in ini.splitlines():
        line = raw.strip()
        if line.startswith('['): section = line.lower()
        key = line.split('=', 1)[0].strip().lower()
        if section == '[modloading]' and key in {'mode', 'cpu_cache_enabled', 'cpu_cache_mb', 'gpu_cache_enabled', 'gpu_cache_mb', 'startup_gpu_preload'}: continue
        common.append(line)
    preload_path = profile / 'PRELOAD-RESULT.json'
    if preload_path.is_file():
        preload = json.loads(preload_path.read_text(encoding='utf-8'))
        common.append(['preload_policy', preload.get('policy'), preload.get('enabled', False)])
    return {'method': 'file paths, sizes, modification times, INI contents; asset contents not rehashed',
        'file_count': len(entries),
        'identity': hashlib.sha256(json.dumps([entries, common], sort_keys=True).encode()).hexdigest()}


def quantiles(values):
    ordered = sorted(values)
    def percentile(p):
        index = (len(ordered) - 1) * p
        lower = int(index); upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
    return {'count': len(values), 'total_ms': sum(values), 'median_ms': statistics.median(values),
            'p95_ms': percentile(.95), 'p99_ms': percentile(.99), 'max_ms': ordered[-1]} if values else {'count': 0}


def parse_trace(text):
    footer = next((line for line in reversed(text.splitlines()) if line.startswith('# capture_end=')), '')
    metadata = dict(part.split('=', 1) for part in footer.removeprefix('# ').split(';') if '=' in part)
    rows = list(csv.DictReader(io.StringIO('\n'.join(line for line in text.splitlines() if not line.startswith('#')))))
    stages = {}; presents = []; cache_hits = 0; gpu_hits = 0
    for row in rows:
        if row['capture_session'] != metadata.get('capture_end', row['capture_session']):
            raise ValueError('Mixed trace sessions')
        if row['kind'] == 'duration':
            stages.setdefault(row['stage'], []).append(int(row['duration_ns']) / 1e6)
        if row['stage'] == 'present_cpu': presents.append(row)
        if row['stage'] == 'shader_cache_hit': cache_hits += 1
        if row['stage'] == 'readiness_wait' and row['kind'] == 'instant' and row['result'] == '0': gpu_hits += 1
    # Native Present records are CPU call intervals, not displayed-frame timing.
    chains = {}
    for row in presents: chains.setdefault(row['object_id'], []).append(row)
    primary = max(chains, key=lambda key: len(chains[key])) if chains else None
    presents = chains.get(primary, [])
    presents.sort(key=lambda row: int(row['start_ns']))
    intervals = []; gaps = 0
    for before, after in zip(presents, presents[1:]):
        if int(after['start_frame']) != int(before['start_frame']) + 1:
            gaps += 1; continue
        intervals.append((int(after['start_ns']) - int(before['start_ns'])) / 1e6)
    valid = (metadata.get('lossless') == '1' and metadata.get('structural_complete') == '1'
        and metadata.get('accounting_available') == '1' and metadata.get('reason') == 'scheduled_stop'
        and all(metadata.get(key) == '0' for key in ['dropped_full', 'dropped_contention', 'stale', 'outstanding'])
        and all(metadata.get(key) == str(len(rows)) for key in ['accepted', 'drained', 'written']))
    return {'complete_lossless': valid, 'footer': metadata, 'rows': len(rows),
        'stages': {key: quantiles(values) for key, values in stages.items()}, 'shader_cache_hits': cache_hits, 'prepared_gpu_hits': gpu_hits,
        'present_intervals': quantiles(intervals), 'present_frame_gaps': gaps, 'primary_swap_chain': primary,
        'trace_to_first_present_ms': ((int(presents[0]['start_ns']) - min(int(r['start_ns']) for r in rows)) / 1e6) if presents else None,
        'scope': 'Native stage durations can overlap; never sum stages. Present intervals are CPU pacing, not display times. First-correct-mod appearance and interactive-ready startup require visual confirmation.'}


def compare(directory):
    paths = [directory / (mode + '.json') for mode in ['baseline', 'optimized']]
    if not all(path.exists() for path in paths): return
    baseline, optimized = [json.loads(path.read_text()) for path in paths]
    reasons = []
    for run in [baseline, optimized]:
        if run.get('status') != 'complete': reasons.append(run['mode'] + ': capture incomplete')
        if not run.get('dll', {}).get('verified'): reasons.append(run['mode'] + ': loaded DLL unverified')
        if not run.get('trace', {}).get('complete_lossless'): reasons.append(run['mode'] + ': native events missing or lost')
        if not run.get('trace', {}).get('present_intervals', {}).get('count'): reasons.append(run['mode'] + ': no frame intervals')
        if not run.get('trace', {}).get('stages', {}).get('config_reload', {}).get('count'): reasons.append(run['mode'] + ': no F10 reload captured')
        if run.get('before') != run.get('after'): reasons.append(run['mode'] + ': assets/settings changed during run')
    if baseline.get('before') != optimized.get('before'): reasons.append('Mods/settings metadata differs between runs')
    if baseline.get('expected_dll') != optimized.get('expected_dll'): reasons.append('DLLs differ')
    changes = {}
    if not reasons:
        for label in ['file_read', 'file_load_combined', 'gpu_creation', 'config_reload']:
            a = baseline['trace']['stages'].get(label, {}); b = optimized['trace']['stages'].get(label, {})
            if a.get('median_ms', 0) and b.get('count'):
                changes[label] = {'baseline': a, 'optimized': b,
                    'median_reduction_percent': 100 * (1 - b['median_ms'] / a['median_ms'])}
        a = baseline['trace']['present_intervals']; b = optimized['trace']['present_intervals']
        changes['present_intervals'] = {'baseline': a, 'optimized': b,
            'p95_reduction_percent': 100 * (1 - b['p95_ms'] / a['p95_ms']) if a['p95_ms'] else None}
    write_json(directory / 'comparison.json', {'matched_capture_pair': not reasons, 'issues': reasons,
        'measurements': changes, 'verdict': 'One user-driven pair is preliminary; matching route and first-correct-mod appearance require user confirmation. No instantaneous-loading claim.'})


def capture(args):
    root = args.root.resolve(); profile = root / '.collector-data/collector-auto-20261023/diagnostic-launcher'
    destination = root / 'captures/loading-speed' / args.dll_hash[:12]; destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / (args.mode + '.json')
    report = {'mode': args.mode, 'status': 'waiting_for_game', 'expected_dll': args.dll_hash,
              'started_unix': time.time(), 'game_launched_by_observer': False, 'uac_requested': False}
    try:
        preload_path = profile / 'PRELOAD-RESULT.json'
        if preload_path.is_file(): report['prelaunch_model_preload'] = json.loads(preload_path.read_text(encoding='utf-8'))
        report['before'] = fingerprint(profile)
        write_json(report_path, report)
        # Readiness handshake: the entry opens XXMI only once the observer is ready.
        write_json(Path(args.ready), {'ready': True, 'since': args.since})
        deadline = time.monotonic() + 900; selected = None
        while time.monotonic() < deadline:
            try:
                current = json.loads(Path(args.ready).read_text())
                if abs(float(current.get('since', 0)) - args.since) > .01:
                    return  # A new user-selected run superseded this observer.
            except (OSError, ValueError):
                time.sleep(.1); continue
            candidates = [p for p in (profile / 'ZZMI').glob('mod-load-trace-*.csv') if p.stat().st_ctime >= args.since]
            if selected is None and candidates:
                selected = min(candidates, key=lambda p: p.stat().st_ctime)
                pid = int(re.fullmatch(r'mod-load-trace-(\d+)-\d+-\d+\.csv', selected.name)[1])
                report.update(status='recording', source=selected.name, pid=pid)
            if selected:
                try:
                    status = json.loads((profile / 'ZZMI/.xxmi-preparation/status.json').read_text())
                    if status.get('process_id') == report['pid'] and status.get('dll_sha256') == args.dll_hash:
                        report['preparation_last'] = status
                        report['cpu_ready_peak'] = max(report.get('cpu_ready_peak', 0), status.get('cpu_ready', 0))
                        report['gpu_ready_peak'] = max(report.get('gpu_ready_peak', 0), status.get('gpu_ready', 0))
                except (OSError, ValueError): pass
                if not report.get('dll', {}).get('verified'):
                    try: report['dll'] = inspect_dll(report['pid'], profile / 'ZZMI/d3d11.dll', args.dll_hash)
                    except OSError as error: report['dll'] = {'verified': False, 'error': str(error)}
                # A completed footer is small and final; avoid parsing a growing CSV every poll.
                with selected.open('rb') as stream:
                    stream.seek(0, 2); stream.seek(max(0, stream.tell() - 2048))
                    tail = stream.read().decode('utf-8', errors='replace')
                if '# capture_end=' in tail:
                    report['trace'] = parse_trace(selected.read_text(encoding='utf-8'))
                    report['after'] = fingerprint(profile)
                    report['status'] = 'complete' if report['trace']['complete_lossless'] and report.get('dll', {}).get('verified') else 'incomplete'
                    # Retain exactly one CSV per preset; retry briefly if native close is pending.
                    for attempt in range(20):
                        try: selected.replace(destination / (args.mode + '.csv')); break
                        except PermissionError:
                            if attempt == 19: raise
                            time.sleep(.1)
                    break
            write_json(report_path, report)
            time.sleep(1)
        else:
            report.update(status='incomplete', error='No completed three-minute native capture within 15 minutes. Keep the game open for the full capture window.')
        report['finished_unix'] = time.time()
    except Exception as error:
        report.update(status='error', error=str(error))
        if not Path(args.ready).exists(): write_json(Path(args.ready), {'ready': False, 'error': str(error)})
    write_json(report_path, report)
    compare(destination)
    message = ('capture saved. You may now close Zenless and XXMI.' if report['status'] == 'complete'
               else 'capture could not be qualified. The report contains the reason.')
    ctypes.windll.user32.MessageBoxW(None, args.mode.title() + ' ' + message + '\n\n' + str(destination),
                                   'XXMI loading comparison', 0x40)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--mode', choices=['baseline', 'optimized'], required=True)
    parser.add_argument('--dll-hash', required=True)
    parser.add_argument('--since', type=float, required=True)
    parser.add_argument('--ready', required=True)
    capture(parser.parse_args())


if __name__ == '__main__': main()
