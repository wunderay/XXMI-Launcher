"""Synthetic importer contract tests. These fixtures are NOT game qualification."""
import csv
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/PreparedLoading/qualify.py'
spec = importlib.util.spec_from_file_location('prepared_qualify', SCRIPT)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.fixture()

    def artifact(self, name, data):
        if isinstance(data, dict):
            data = json.dumps(data).encode()
        if isinstance(data, str):
            data = data.encode()
        (self.root / name).write_bytes(data)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    def fixture(self):
        shared = {key: self.artifact(key, 'synthetic ' + key) for key in q.CONDITIONS[5:]}
        shared.update(importer='ZZMI', importer_version='synthetic-1', game_version='synthetic',
                      hardware='synthetic hardware', driver='synthetic driver')
        runs = []
        for name in ('baseline', 'candidate'):
            out = io.StringIO(newline='')
            writer = csv.writer(out, lineterminator='\n')
            writer.writerow(q.COLUMNS)
            for i in range(100):
                writer.writerow([str(i), 'outfit-toggle', 'warm-gpu', .2, 10 if name == 'baseline' else 10.4,
                                 i, i, 0, 0, 0, 0, 0, 0])
            run = dict(shared, source_sha=('1' if name == 'baseline' else '2') * 40,
                       source_archive=self.artifact(name + '.source', 'synthetic source ' + name),
                       patch=self.artifact(name + '.patch', 'synthetic patch ' + name),
                       dll=self.artifact(name + '.dll', 'synthetic DLL ' + name),
                       samples=self.artifact(name + '.csv', out.getvalue()),
                       trace=self.artifact(name + '.trace', ','.join(q.TRACE_COLUMNS)+'\nactivation,instant,1,1,0,1,1,1,1,0,\n# cumulative_accepted=1;dropped_full=0;dropped_contention=0;drained=1\n'))
            run['compiler'] = self.artifact(name + '.compiler', dict(source_sha=run['source_sha'],
                source_archive_sha256=run['source_archive']['sha256'], patch_sha256=run['patch']['sha256'],
                dll_sha256=run['dll']['sha256'], compiler_id='synthetic compiler', flags='/synthetic',
                architecture='x64', build_configuration='synthetic', build_command='synthetic fixture'))
            run['capture'] = self.artifact(name + '.capture.json', dict(
                origin='synthetic-test', trace_sha256=run['trace']['sha256'],
                samples_sha256=run['samples']['sha256'], loaded_dll_sha256=run['dll']['sha256'],
                collector_version='synthetic-1', capture_id=name, started_utc='2026-10-24T00:00:00Z',
                ended_utc='2026-10-24T00:01:00Z', process_id=1, loaded_dll_path='synthetic.dll',
                normalizer_version='synthetic-1', normalization_command='synthetic generator',
                measurement_scope='all-activation-path-operations-and-paired-frames',
                size_limit_reached=False, malformed_rows=0))
            runs.append(run)
        raw = self.artifact('recovery.raw', 'synthetic observed recovery transcript')
        events = []
        for kind, run in [('candidate-failure-retained-generation', runs[1]), ('rollback-restored-prior', runs[0])]:
            events.append(dict(event=kind, observed_dll_sha256=run['dll']['sha256'],
                               previous_generation='generation-1', observed_generation='generation-1',
                               utc='2026-10-24T00:02:00Z', capture_id='recovery', reason='synthetic injected failure',
                               observation='synthetic render retained complete generation', capture_sha256=raw['sha256']))
        recovery = self.artifact('recovery.json', dict(origin='synthetic-test',
            candidate_dll_sha256=runs[1]['dll']['sha256'], prior_dll_sha256=runs[0]['dll']['sha256'],
            event_log=self.artifact('recovery.jsonl', '\n'.join(map(json.dumps, events))+'\n'),
            capture_ids=['recovery'], captures=[raw]))
        review = self.artifact('review.json', dict(origin='synthetic-test', reviewer='Synthetic fixture only',
            reviewed_utc='2026-10-24T00:03:00Z', independence_basis='synthetic fixture has no real reviewer',
            visual_observation='synthetic full replacement', first_eligible_frame_observation='synthetic frame identity',
            verdict='accept', appearance='correct-complete', timing='first-eligible-frame',
            scenarios=['outfit-toggle'], candidate_dll_sha256=runs[1]['dll']['sha256'],
            baseline_dll_sha256=runs[0]['dll']['sha256'], samples_sha256=runs[1]['samples']['sha256'],
            trace_sha256=runs[1]['trace']['sha256'], recovery_sha256=recovery['sha256']))
        return dict(schema_version=1, origin='synthetic-test', baseline=runs[0], candidate=runs[1],
                    scenarios=[dict(name='outfit-toggle', cache_state='warm-gpu')], recovery=recovery, review=review)

    def evaluate(self):
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(self.manifest))
        return q.qualify(path)

    def replace_artifact(self, owner, field, transform):
        ref = owner[field]
        data = (self.root / ref['path']).read_text()
        owner[field] = self.artifact(ref['path'], transform(data))

    def issues(self, report):
        return ' '.join(g['name'] for g in report['gates'])

    def test_valid_synthetic_fixture_diagnostic_only(self):
        report = self.evaluate()
        self.assertTrue(report['diagnostic_collection_pass'], report)
        self.assertFalse(report['prepared_optimization_qualified'])
        self.assertEqual(report['supported_matrix'], [])
        self.assertIn('NOT game qualification', self.issues(report))
        stats = report['scenarios'][0]['paired_added_frame_ms']
        self.assertAlmostEqual(stats['p99'], .4)
        self.assertIsNone(stats['uncertainty']['p99_upper_ms'])

    def test_digest_tampering_and_missing_trace_fail_closed(self):
        (self.root / 'candidate.dll').write_bytes(b'tampered')
        self.assertIn('digest mismatch', self.issues(self.evaluate()))

    def test_dropped_trace_and_truncation_fail_closed(self):
        for transform in (lambda x: x.replace('dropped_full=0', 'dropped_full=1'), lambda x: x.rsplit('\n', 2)[0]+'\n',
                          lambda x: x.replace('drained=1', 'drained=2')):
            with self.subTest(transform=transform):
                self.manifest = self.fixture()
                self.replace_artifact(self.manifest['candidate'], 'trace', transform)
                self.assertFalse(self.evaluate()['diagnostic_collection_pass'])

    def streaming_trace(self, foreign_discarded=0):
        # Exact native TraceControl.cpp capture_v2 grammar; not collector 0.1.6.
        values = dict(capture_end=3, structural_complete=1, lossless=1, accepted=1,
            drained=1, written=1, dropped_full=0, dropped_contention=0, stale=0,
            outstanding=0, foreign_discarded=foreign_discarded, accounting_available=1,
            reason='stopped')
        return ('\n'.join(q.TRACE_PREAMBLE) + '\n' + ','.join(q.TRACE_COLUMNS) +
            '\n# capture_begin=3;accounting=capture_v2\n' +
            'activation,instant,3,1,0,1,1,1,1,0,\n# ' +
            ';'.join(key+'='+str(values[key]) for key in q.STREAM_FIELDS) + '\n')

    def set_candidate_trace(self, text):
        run = self.manifest['candidate']
        run['trace'] = self.artifact(run['trace']['path'], text)
        self.refresh_capture(run)

    def test_native_v1_preamble_keeps_strict_lifetime_audit(self):
        run = self.manifest['candidate']
        original = (self.root/run['trace']['path']).read_text()
        self.set_candidate_trace('\n'.join(q.TRACE_PREAMBLE)+'\n'+original)
        self.assertTrue(self.evaluate()['diagnostic_collection_pass'])
        self.set_candidate_trace('\n'.join(q.TRACE_PREAMBLE)+'\n'+original.replace('dropped_contention=0', 'dropped_contention=91'))
        self.assertFalse(self.evaluate()['diagnostic_collection_pass'])

    def test_streaming_current_session_ignores_discarded_older_records(self):
        self.set_candidate_trace(self.streaming_trace(foreign_discarded=8000))
        self.assertTrue(self.evaluate()['diagnostic_collection_pass'])
        self.set_candidate_trace(self.streaming_trace().replace('activation,instant,3,', 'activation,instant,1,'))
        self.assertIn('foreign session row', self.issues(self.evaluate()))

    def test_streaming_structural_footer_cannot_assert_lossless(self):
        transforms = {
            'full': lambda s: s.replace('dropped_full=0', 'dropped_full=1'),
            'contention': lambda s: s.replace('dropped_contention=0', 'dropped_contention=1'),
            'stale': lambda s: s.replace('stale=0', 'stale=1'),
            'outstanding': lambda s: s.replace('outstanding=0', 'outstanding=1'),
            'unavailable': lambda s: s.replace('accounting_available=1', 'accounting_available=0'),
            'lossy': lambda s: s.replace('lossless=1', 'lossless=0'),
            'structural': lambda s: s.replace('structural_complete=1', 'structural_complete=0'),
            'missing-field': lambda s: s.replace(';stale=0', ''),
            'partial-footer': lambda s: s.rsplit(';reason=', 1)[0]+'\n',
            'no-footer': lambda s: '\n'.join(s.splitlines()[:-1])+'\n',
            'not-stopped': lambda s: s.replace('reason=stopped', 'reason=time_limit'),
            'wrong-session': lambda s: s.replace('capture_end=3', 'capture_end=5'),
            'missing-begin': lambda s: s.replace('# capture_begin=3;accounting=capture_v2\n', ''),
            'written': lambda s: s.replace('written=1', 'written=2'),
            'drained': lambda s: s.replace('drained=1', 'drained=2'),
            'accepted': lambda s: s.replace('accepted=1', 'accepted=2'),
            'mixed-accounting': lambda s: s.replace('# capture_end=', '# cumulative_accepted=1;dropped_full=99;dropped_contention=0;drained=1\n# capture_end='),
        }
        for case, transform in transforms.items():
            with self.subTest(case=case):
                self.manifest = self.fixture()
                self.set_candidate_trace(transform(self.streaming_trace()))
                self.assertFalse(self.evaluate()['diagnostic_collection_pass'])

    def test_conditions_scenarios_and_other_importer_fail_closed(self):
        for field, value in [('driver', 'other'), ('importer', 'GIMI')]:
            with self.subTest(field=field):
                self.manifest = self.fixture()
                self.manifest['candidate'][field] = value
                self.assertFalse(self.evaluate()['diagnostic_collection_pass'])
        self.manifest = self.fixture()
        self.manifest['baseline']['importer'] = self.manifest['candidate']['importer'] = 'GIMI'
        self.assertIn('qualify individually', self.issues(self.evaluate()))
        self.manifest = self.fixture()
        self.manifest['scenarios'][0]['name'] = 'unmeasured'
        self.assertFalse(self.evaluate()['diagnostic_collection_pass'])

    def refresh_capture(self, run):
        ref = run['capture']
        capture = json.loads((self.root / ref['path']).read_text())
        capture.update(samples_sha256=run['samples']['sha256'], trace_sha256=run['trace']['sha256'])
        run['capture'] = self.artifact(ref['path'], capture)

    def test_too_few_samples_and_paired_stats(self):
        for run in (self.manifest['baseline'], self.manifest['candidate']):
            self.replace_artifact(run, 'samples', lambda x: '\n'.join(x.splitlines()[:-1])+'\n')
            self.refresh_capture(run)
        report = self.evaluate()
        self.assertTrue(report['diagnostic_collection_pass'])
        self.assertIn('fewer than 100', self.issues(report))
        # Pairwise p99 differs from subtracting independent p99s.
        self.manifest = self.fixture()
        for name in ('baseline', 'candidate'):
            run = self.manifest[name]
            rows = list(csv.reader((self.root / run['samples']['path']).read_text().splitlines()))
            for i, row in enumerate(rows[1:]):
                row[4] = str((100 if i < 50 else 10) if name == 'baseline' else (10 if i < 50 else 100))
            out = io.StringIO(); writer = csv.writer(out, lineterminator='\n'); writer.writerows(rows)
            run['samples'] = self.artifact(run['samples']['path'], out.getvalue())
            self.refresh_capture(run)
        report = self.evaluate()
        self.assertEqual(report['scenarios'][0]['paired_added_frame_ms']['p99'], 90)
        self.assertIn('investigation', self.issues(report))

    def test_loaded_dll_and_visual_attestation_required(self):
        run = self.manifest['candidate']
        self.replace_artifact(run, 'capture', lambda x: x.replace(run['dll']['sha256'], '0'*64))
        self.assertIn('loaded DLL mismatch', self.issues(self.evaluate()))
        self.manifest = self.fixture()
        self.manifest.pop('review')
        self.assertFalse(self.evaluate()['prepared_optimization_qualified'])

    def test_operations_and_late_appearance_fail_even_with_fast_frames(self):
        for column, value, issue in [('read', '1', 'activation path'),
                                      ('visible_frame', '9999', 'first eligible frame')]:
            with self.subTest(column=column):
                self.manifest = self.fixture()
                run = self.manifest['candidate']
                rows = list(csv.reader((self.root/run['samples']['path']).read_text().splitlines()))
                rows[1][q.COLUMNS.index(column)] = value
                out = io.StringIO(); csv.writer(out, lineterminator='\n').writerows(rows)
                run['samples'] = self.artifact(run['samples']['path'], out.getvalue())
                self.refresh_capture(run)
                self.assertIn(issue, self.issues(self.evaluate()))

    def test_unpaired_row_compiler_mismatch_and_path_escape_rejected(self):
        run = self.manifest['candidate']
        self.replace_artifact(run, 'samples', lambda x: x.replace('outfit-toggle', 'character-revisit', 1))
        self.refresh_capture(run)
        self.assertIn('paired scenario/sample mismatch', self.issues(self.evaluate()))
        self.manifest = self.fixture()
        run = self.manifest['candidate']
        self.replace_artifact(run, 'compiler', lambda x: x.replace(run['source_sha'], '0'*40))
        self.assertIn('build provenance mismatch', self.issues(self.evaluate()))
        self.manifest = self.fixture()
        self.manifest['candidate']['dll']['path'] = '../escape.dll'
        self.assertIn('outside bundle or missing', self.issues(self.evaluate()))

    def test_recovery_boolean_is_not_evidence(self):
        self.manifest['recovery'] = self.artifact('recovery.json', {'passed': True})
        self.assertFalse(self.evaluate()['prepared_optimization_qualified'])
        self.assertTrue(any(g['status']=='fail' for g in self.evaluate()['gates']))

    def test_mock_actual_origin_cannot_skip_full_release_matrix(self):
        # Mock metadata exercises the release branch only. This remains a
        # synthetic unit test; its return value is never published as evidence.
        self.manifest['origin'] = 'actual-game'
        for run in (self.manifest['baseline'], self.manifest['candidate']):
            self.replace_artifact(run, 'capture', lambda x: x.replace('synthetic-test', 'actual-game'))
        self.replace_artifact(self.manifest, 'recovery', lambda x: x.replace('synthetic-test', 'actual-game'))
        ref = self.manifest['review']
        review = json.loads((self.root/ref['path']).read_text())
        review.update(origin='actual-game', recovery_sha256=self.manifest['recovery']['sha256'])
        self.manifest['review'] = self.artifact(ref['path'], review)
        report = self.evaluate()
        self.assertTrue(report['prepared_optimization_qualified'])
        self.assertFalse(report['release_qualified'])
        self.assertEqual(len(report['supported_matrix']), 1)
        self.assertIn('release_coverage', self.issues(report))
        self.manifest['release_coverage'] = self.artifact('coverage.json', dict(
            origin='actual-game', candidate_dll_sha256=self.manifest['candidate']['dll']['sha256'], cases={}))
        report = self.evaluate()
        self.assertFalse(report['release_qualified'])
        self.assertIn('full release validation matrix pending', self.issues(report))

    def test_cli_reports_nonzero_for_synthetic_fixture(self):
        self.evaluate()
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.root/'manifest.json'),
                                 '--output-dir', str(self.root/'report')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.root/'report/qualification.json').is_file())
        self.assertTrue((self.root/'report/qualification.md').is_file())


if __name__ == '__main__':
    unittest.main()
