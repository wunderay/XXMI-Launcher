#!/usr/bin/env python3
"""Offline evidence gate. Never launches games, installs, signs, or contacts services.

Inputs are evidence assertions, not authentication: retain and independently review
the original captures. Synthetic inputs can exercise this gate but never qualify.
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import math
import re
from pathlib import Path

MAX_FILE = 64 * 1024 * 1024
MAX_ROWS = 250000
SHA = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_SHA = re.compile(r"[0-9a-f]{40}\Z")
FORBIDDEN = ('file_op', 'read', 'decode', 'compile', 'create', 'worker_wait')
COLUMNS = ['pair_id', 'scenario', 'cache_state', 'activation_ms', 'frame_ms',
           'eligible_frame', 'visible_frame', *FORBIDDEN]
TRACE_COLUMNS = 'stage,kind,capture_session,start_ns,duration_ns,start_frame,end_frame,thread_id,object_id,bytes,result'.split(',')
TRACE_PREAMBLE = ['# load_trace_v1; monotonic_ns; frame_ids_are_attribution_only',
                  '# activation_and_first_correct_frame_require_explicit_external_evidence']
STREAM_FIELDS = ('capture_end', 'structural_complete', 'lossless', 'accepted', 'drained',
                 'written', 'dropped_full', 'dropped_contention', 'stale', 'outstanding',
                 'foreign_discarded', 'accounting_available', 'reason')
CONDITIONS = ('importer', 'importer_version', 'game_version', 'hardware', 'driver',
              'mod_manifest', 'settings', 'config', 'scenario_protocol', 'instrumentation')
RELEASE_CASES = (
    'small-library', 'large-library', 'mesh-heavy', 'texture-heavy', 'alternate-outfits',
    'lod-0-1-2', 'facial-animation', 'compute-morph', 'shared-libraries', 'duplicate-overrides',
    'persistent-settings', 'cold-os-cache', 'warm-os-empty-xxmi-cache', 'warm-cpu-no-gpu',
    'warm-gpu', 'process-start', 'device-recreation', 'multiple-resolutions-and-frame-targets',
    'memory-pressure-and-generation-peak', 'rapid-f10', 'no-change-refresh', 'force-reset',
    'deletion', 'symlinks-junctions-aliases', 'unicode-paths', 'huge-corrupt-assets',
    'cache-tampering', 'disk-full', 'cancellation', 'allocation-failure', 'stale-completion',
    'root-replacement', 'device-loss', 'new-or-modified-mod', 'first-character-appearance',
    'character-revisit', 'outfit-toggle', 'lod-transition')


class EvidenceError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def read_json(path):
    require(path.stat().st_size <= MAX_FILE, 'JSON exceeds file bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


class Importer:
    def __init__(self, manifest):
        self.root = manifest.parent.resolve()
        self.used = {}
        self.total_bytes = 0

    def artifact(self, ref):
        require(isinstance(ref, dict), 'artifact reference required')
        name, digest = ref.get('path'), ref.get('sha256')
        require(isinstance(name, str) and not Path(name).is_absolute(), 'relative artifact path required')
        require(isinstance(digest, str) and SHA.fullmatch(digest), 'SHA256 required: ' + str(name))
        path = (self.root / name).resolve()
        require(path.is_relative_to(self.root) and path.is_file(), 'artifact outside bundle or missing: ' + name)
        require(path.stat().st_size <= MAX_FILE, 'artifact exceeds 64 MiB bound: ' + name)
        if name not in self.used:
            self.total_bytes += path.stat().st_size
            require(len(self.used) < 512 and self.total_bytes <= 512 * 1024 * 1024,
                    'evidence bundle exceeds artifact/count bounds')
        data = path.read_bytes()
        require(hashlib.sha256(data).hexdigest() == digest, 'artifact digest mismatch: ' + name)
        self.used[name] = digest
        return data

    def json(self, ref):
        self.artifact(ref)
        return read_json(self.root / ref['path'])


def number(value):
    result = float(value)
    require(math.isfinite(result) and result >= 0, 'nonfinite/negative measurement')
    return result


def integer(value):
    require(str(value).isdigit(), 'nonnegative integer required')
    return int(value)


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def stats(values):
    # Distribution-free simultaneous CDF bound, not a false precise p99 CI.
    n = len(values)
    epsilon = math.sqrt(math.log(40) / (2 * n))
    upper_p = .99 + epsilon
    return dict(n=n, p50=percentile(values, .5), p95=percentile(values, .95),
                p99=percentile(values, .99), maximum=max(values),
                uncertainty={'method': '95% DKW distribution-free CDF band',
                             'cdf_epsilon': epsilon,
                             'p99_lower_ms': percentile(values, max(0, .99-epsilon)),
                             'p99_upper_ms': percentile(values, upper_p) if upper_p <= 1 else None,
                             'note': '100 samples give a preliminary tail estimate; null upper bound is unbounded.'})


def samples(importer, run):
    data = importer.artifact(run['samples']).decode('utf-8')
    require(data.endswith('\n'), 'truncated sample CSV')
    reader = csv.DictReader(io.StringIO(data), strict=True)
    require(reader.fieldnames == COLUMNS, 'sample CSV header mismatch')
    rows = {}
    for row in reader:
        require(len(rows) < MAX_ROWS, 'too many sample rows')
        require(None not in row and all(v is not None for v in row.values()), 'malformed sample CSV')
        key = (row['scenario'], row['cache_state'], row['pair_id'])
        require(all(key) and key not in rows, 'empty/duplicate sample identity')
        for field in ('activation_ms', 'frame_ms'):
            row[field] = number(row[field])
        for field in ('eligible_frame', 'visible_frame', *FORBIDDEN):
            row[field] = integer(row[field])
        rows[key] = row
    require(rows, 'empty sample CSV')
    return rows


def lossless(importer, run):
    """Audit native standalone v1 or per-capture streaming v2 accounting.

    Standalone lifetime counters must match every row. Streaming counters cover
    only the named session; older-session drops do not poison this capture.
    """
    text = importer.artifact(run['trace']).decode('utf-8')
    lines = text.splitlines()
    require(text.endswith('\n') and len(lines) >= 3, 'truncated/empty trace')
    streaming = lines[-1].startswith('# capture_end=')
    session = None
    if streaming:
        require(lines[:2] == TRACE_PREAMBLE and len(lines) >= 6, 'missing streaming trace preamble')
        begin = re.fullmatch(r'# capture_begin=(\d+);accounting=capture_v2', lines[3])
        require(begin is not None, 'missing streaming capture begin')
        session = int(begin.group(1))
        require(0 < session < 2**64 and session % 2 == 1, 'invalid streaming capture session')
        pairs = [part.split('=', 1) for part in lines[-1][2:].split(';')]
        require(all(len(pair) == 2 for pair in pairs) and
                tuple(pair[0] for pair in pairs) == STREAM_FIELDS, 'incomplete streaming trace footer')
        footer = dict(pairs)
        values = {key: integer(footer[key]) for key in STREAM_FIELDS[:-1]}
        require(all(value < 2**64 for value in values.values()), 'streaming counter exceeds uint64')
        require(values['capture_end'] == session, 'streaming capture session mismatch')
        require(values['structural_complete'] == 1, 'streaming footer not structurally complete')
        require(values['accounting_available'] == 1, 'streaming capture accounting unavailable')
        require(values['lossless'] == 1 and footer['reason'] == 'stopped', 'streaming capture is not lossless/stopped')
        require(all(values[key] == 0 for key in ('dropped_full', 'dropped_contention', 'stale', 'outstanding')),
                'streaming capture lost/stale/outstanding events')
        accepted, drained = values['accepted'], values['drained']
        written = values['written']
        # foreign_discarded belongs to older sessions. It is recorded, but the
        # exact current-session counts and row identities below are the gate.
        body = [lines[2], *lines[4:-1]]
    else:
        match = re.fullmatch(r'# cumulative_accepted=(\d+);dropped_full=(\d+);dropped_contention=(\d+);drained=(\d+)', lines[-1])
        require(match is not None, 'missing complete lossless trace footer')
        accepted, full, contention, drained = map(int, match.groups())
        require(full == contention == 0, 'dropped trace events')
        written = drained
        body = lines[2:-1] if lines[:2] == TRACE_PREAMBLE else lines[:-1]
    require(not any(line.startswith('#') for line in body), 'unexpected trace footer/comment')
    reader = csv.reader(body, strict=True)
    header = next(reader)
    require(header == TRACE_COLUMNS, 'invalid native trace header')
    count = 0
    for row in reader:
        require(len(row) == len(header) and all(row[:2]), 'malformed native trace row')
        require(row[1] in ('instant', 'duration'), 'unknown trace event kind')
        for value in row[2:10]:
            integer(value)
        if session is not None:
            require(integer(row[2]) == session, 'foreign session row in streaming capture')
        require(not row[10] or row[10].lstrip('-').isdigit(), 'invalid trace result')
        count += 1
        require(count <= MAX_ROWS, 'too many trace rows')
    require(count > 0 and accepted == drained == written == count, 'incomplete trace event counts')
    meta = importer.json(run['capture'])
    require(meta['trace_sha256'] == run['trace']['sha256'], 'capture/trace mismatch')
    require(meta['samples_sha256'] == run['samples']['sha256'], 'capture/sample mismatch')
    require(meta['loaded_dll_sha256'] == run['dll']['sha256'], 'actual loaded DLL mismatch')
    for key in ('collector_version', 'capture_id', 'started_utc', 'ended_utc', 'process_id',
                'loaded_dll_path', 'normalizer_version', 'normalization_command'):
        require(bool(meta.get(key)), 'capture provenance missing: ' + key)
    start = datetime.datetime.fromisoformat(meta['started_utc'].replace('Z', '+00:00'))
    end = datetime.datetime.fromisoformat(meta['ended_utc'].replace('Z', '+00:00'))
    require(start.utcoffset() == end.utcoffset() == datetime.timedelta(0) and end > start,
            'capture timestamps must be ordered UTC')
    require(integer(meta['process_id']) > 0, 'invalid process identity')
    require(meta.get('measurement_scope') == 'all-activation-path-operations-and-paired-frames',
            'capture does not measure every activation operation and frame')
    require(meta.get('size_limit_reached') is False and meta.get('malformed_rows') == 0,
            'capture size limit/malformed rows')
    return meta


def qualify(path):
    report = {'schema_version': 1, 'diagnostic_collection_pass': False,
              'prepared_optimization_qualified': False, 'release_qualified': False,
              'gates': [], 'scenarios': [],
              'supported_matrix': [], 'limitations': [
                  'Offline validation checks supplied evidence integrity and consistency; it cannot authenticate a game capture or reviewer identity.',
                  'Performance thresholds use observed empirical p99; uncertainty is reported and may be large.',
                  'Qualification applies only to the exact importer/version, scenarios and conditions listed. No publisher signing or release authorization is performed.']}
    importer = Importer(Path(path))
    try:
        manifest = read_json(Path(path))
        require(manifest['schema_version'] == 1, 'unsupported manifest schema')
        require(manifest['origin'] in ('actual-game', 'synthetic-test'), 'unknown evidence origin')
        report['origin'] = manifest['origin']
        report['manifest_sha256'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        runs = [manifest['baseline'], manifest['candidate']]
        captures = []
        sets = []
        for run in runs:
            require(SOURCE_SHA.fullmatch(run['source_sha']) is not None, 'source SHA required')
            for name in ('source_archive', 'patch', 'dll', 'compiler', *CONDITIONS[5:]):
                importer.artifact(run[name])
            build = importer.json(run['compiler'])
            for key, expected_value in (('source_sha', run['source_sha']),
                    ('source_archive_sha256', run['source_archive']['sha256']),
                    ('patch_sha256', run['patch']['sha256']), ('dll_sha256', run['dll']['sha256'])):
                require(build.get(key) == expected_value, 'build provenance mismatch: ' + key)
            for key in ('compiler_id', 'flags', 'architecture', 'build_configuration', 'build_command'):
                require(bool(build.get(key)), 'compiler configuration missing: ' + key)
            for name in CONDITIONS[:5]:
                require(bool(run[name]), 'missing run identity: ' + name)
            captures.append(lossless(importer, run))
            sets.append(samples(importer, run))
        require(all(c.get('origin') == manifest['origin'] for c in captures), 'capture origin mismatch')
        require(captures[0]['capture_id'] != captures[1]['capture_id'], 'baseline/candidate same capture')
        for key in CONDITIONS:
            require(runs[0][key] == runs[1][key], 'paired conditions mismatch: ' + key)
        require(runs[0]['dll']['sha256'] != runs[1]['dll']['sha256'], 'baseline/candidate DLLs identical')
        require(runs[1]['importer'] == 'ZZMI', 'importer has no qualified protocol; qualify individually')
        require(set(sets[0]) == set(sets[1]), 'paired scenario/sample mismatch')
        expected = {(x['name'], x['cache_state']) for x in manifest['scenarios']}
        require(len(expected) == len(manifest['scenarios']) and expected, 'duplicate/empty scenario claims')
        require(expected == {key[:2] for key in sets[0]}, 'claimed scenarios differ from measured scenarios')
        report['diagnostic_collection_pass'] = True
        report['gates'].append({'name': 'provenance-and-lossless-paired-collection', 'status': 'pass'})
        failures = []
        for scenario, cache in sorted(expected):
            keys = [key for key in sets[0] if key[:2] == (scenario, cache)]
            candidate = [sets[1][key] for key in keys]
            bookkeeping = stats([r['activation_ms'] for r in candidate])
            # Quantile of pairwise frame deltas, never difference of p99s.
            deltas = [sets[1][key]['frame_ms']-sets[0][key]['frame_ms'] for key in keys]
            added = stats(deltas)
            issues = []
            if cache == 'warm-gpu' and len(keys) < 100:
                issues.append('fewer than 100 warm activations')
            if cache != 'warm-gpu':
                issues.append('cold/other cache results diagnostic only; warm-gpu required for this qualification protocol')
            if bookkeeping['p99'] > 1:
                issues.append('bookkeeping p99 exceeds 1 ms')
            if added['p99'] > 1:
                issues.append('paired added frame p99 exceeds 1 ms')
            if max(deltas) > 8:
                issues.append('potential attributable stall over 8 ms requires independent investigation')
            if any(any(r[field] for field in FORBIDDEN) for r in candidate):
                issues.append('activation path contains file/read/decode/compile/create/worker wait work')
            if any(r['visible_frame'] != r['eligible_frame'] for r in candidate):
                issues.append('replacement not complete on first eligible frame')
            report['scenarios'].append(dict(name=scenario, cache_state=cache,
                bookkeeping_ms=bookkeeping, paired_added_frame_ms=added, issues=issues))
            failures.extend(scenario + ': ' + issue for issue in issues)
        report['gates'].extend({'name': issue, 'status': 'pending' if 'investigation' in issue else 'fail'} for issue in failures)
        # Structured event records preserve provenance rather than a success boolean.
        recovery = importer.json(manifest['recovery'])
        require(recovery.get('origin') == manifest['origin'], 'recovery origin mismatch')
        log = importer.artifact(recovery['event_log']).decode('utf-8')
        events = [json.loads(line) for line in log.splitlines()]
        require(len(events) <= MAX_ROWS, 'too many recovery events')
        require(recovery['candidate_dll_sha256'] == runs[1]['dll']['sha256'], 'recovery candidate mismatch')
        require(recovery['prior_dll_sha256'] == runs[0]['dll']['sha256'], 'rollback prior DLL mismatch')
        recovery_digests = {ref['sha256'] for ref in recovery['captures']}
        for kind in ('candidate-failure-retained-generation', 'rollback-restored-prior'):
            matching = [e for e in events if e.get('event') == kind]
            require(matching, 'missing actual recovery evidence: ' + kind)
            for e in matching:
                expected_dll = runs[1 if kind == 'candidate-failure-retained-generation' else 0]['dll']['sha256']
                require(e.get('observed_dll_sha256') == expected_dll, 'recovery DLL mismatch')
                require(e.get('previous_generation') == e.get('observed_generation') and bool(e.get('observed_generation')),
                        'previous generation not preserved/restored')
                for key in ('utc', 'capture_id', 'reason', 'observation'):
                    require(bool(e.get(key)), 'recovery provenance missing: ' + key)
                require(e['capture_id'] in recovery['capture_ids'], 'unbound recovery capture')
                require(e.get('capture_sha256') in recovery_digests, 'recovery event missing raw capture digest')
        for ref in recovery['captures']:
            importer.artifact(ref)
        require(len(recovery['captures']) >= 1, 'missing raw recovery captures')
        review = importer.json(manifest['review'])
        require(review.get('origin') == manifest['origin'], 'review origin mismatch')
        require(review['candidate_dll_sha256'] == runs[1]['dll']['sha256'], 'review candidate mismatch')
        require(review['baseline_dll_sha256'] == runs[0]['dll']['sha256'], 'review baseline mismatch')
        require(review['samples_sha256'] == runs[1]['samples']['sha256'], 'review samples mismatch')
        require(review['trace_sha256'] == runs[1]['trace']['sha256'], 'review trace mismatch')
        require(review['recovery_sha256'] == manifest['recovery']['sha256'], 'review recovery mismatch')
        for key in ('reviewer', 'reviewed_utc', 'independence_basis', 'visual_observation', 'first_eligible_frame_observation'):
            require(isinstance(review.get(key), str) and review[key].strip(), 'review attestation missing: ' + key)
        require(review['verdict'] == 'accept' and review['appearance'] == 'correct-complete' and
                review['timing'] == 'first-eligible-frame', 'review does not attest correct complete first eligible appearance')
        require(sorted(review['scenarios']) == sorted(x['name'] for x in manifest['scenarios']), 'review scenario mismatch')
        if manifest['origin'] != 'actual-game':
            failures.append('synthetic fixture is NOT game qualification')
            report['gates'].append({'name': failures[-1], 'status': 'fail'})
        if not failures:
            report['prepared_optimization_qualified'] = True
            report['gates'].append({'name': 'prepared-optimization-and-independent-acceptance', 'status': 'pass'})
            report['supported_matrix'] = [{key: runs[1][key] for key in CONDITIONS[:5]} | {
                'source_sha': runs[1]['source_sha'], 'dll_sha256': runs[1]['dll']['sha256'],
                'scenarios': manifest['scenarios'], 'manifest_sha256': report['manifest_sha256']}]
            coverage = importer.json(manifest['release_coverage'])
            require(coverage.get('origin') == 'actual-game' and
                    coverage.get('candidate_dll_sha256') == runs[1]['dll']['sha256'], 'release coverage identity mismatch')
            require(set(coverage['cases']) == set(RELEASE_CASES), 'full release validation matrix pending')
            for case, record in coverage['cases'].items():
                require(record.get('verdict') == 'accept', 'release validation pending/fail: ' + case)
                for field in ('observation', 'reviewer', 'reviewed_utc', 'conditions'):
                    require(bool(record.get(field)), 'release case missing provenance: ' + case)
                require(record.get('evidence'), 'release case missing evidence: ' + case)
                for ref in record['evidence']:
                    importer.artifact(ref)
            require(review.get('release_coverage_sha256') == manifest['release_coverage']['sha256'],
                    'independent review does not bind release coverage')
            report['release_qualified'] = True
    except (EvidenceError, KeyError, TypeError, ValueError, OSError, csv.Error,
            OverflowError, AttributeError, RecursionError) as exc:
        report['gates'].append({'name': str(exc), 'status': 'fail'})
    report['verified_artifacts'] = importer.used
    return report


def write_report(report, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    lines = ['# Prepared loading qualification', '',
             'Prepared optimization qualified: **' + str(report['prepared_optimization_qualified']) + '**',
             'Full release qualified: **' + str(report['release_qualified']) + '**',
             'Diagnostic collection pass: **' + str(report['diagnostic_collection_pass']) + '**', '',
             'Synthetic evidence is never real-game qualification.', '', '## Gates', '']
    lines.extend('- ' + gate['status'] + ': ' + gate['name'].replace('\n', ' ') for gate in report['gates'])
    lines.extend(['', '## Scenario statistics', '', '```json', json.dumps(report['scenarios'], indent=2), '```',
                  '', '## Limits', ''])
    lines.extend('- ' + item for item in report['limitations'])
    (output / 'qualification.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    report = qualify(args.manifest)
    write_report(report, args.output_dir)
    print('qualified=' + str(report['prepared_optimization_qualified']))
    return 0 if report['release_qualified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
