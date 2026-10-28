"""Offline collector checks; no game discovery, GUI, elevation or capture loop."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
import time

spec = importlib.util.spec_from_file_location('capture_speed', Path(__file__).with_name('capture-speed.py'))
capture = importlib.util.module_from_spec(spec); spec.loader.exec_module(capture)
HEADER = 'stage,kind,capture_session,start_ns,duration_ns,start_frame,end_frame,thread_id,object_id,bytes,result\n'
ROWS = ('present_cpu,duration,1,1000000,1000,0,1,1,1,0,0\n'
        'present_cpu,duration,1,17000000,1000,1,2,1,1,0,0\n'
        'present_cpu,duration,1,34000000,1000,2,3,1,1,0,0\n'
        'config_reload,duration,1,5000000,500000,1,1,1,0,0,0\n')
FOOTER = '# capture_end=1;structural_complete=1;lossless=1;accepted=4;drained=4;written=4;dropped_full=0;dropped_contention=0;stale=0;outstanding=0;accounting_available=1;reason=scheduled_stop\n'


class CollectorTests(unittest.TestCase):
    def test_gpu_reuse_counts_only_successful_instant_hits(self):
        events = ('readiness_wait,instant,1,35000000,0,3,3,1,12,256,0\n'
                  'readiness_wait,instant,1,36000000,0,3,3,1,13,256,-1\n'
                  'readiness_wait,duration,1,37000000,10,3,3,1,14,256,0\n')
        parsed = capture.parse_trace(HEADER + ROWS + events + FOOTER.replace('=4;', '=7;'))
        self.assertTrue(parsed['complete_lossless'])
        self.assertEqual(parsed['prepared_gpu_hits'], 1)

    def test_gpu_comparison_excludes_only_selected_preset_switches(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary); (profile / 'ZZMI').mkdir()
            ini = profile / 'ZZMI/d3dx.ini'
            ini.write_text('[ModLoading]\nmode = off\ngpu_cache_enabled = 0\ngpu_cache_mb = 0\nstartup_gpu_preload = 0\nstartup_preload_seconds = 30\n')
            baseline = capture.fingerprint(profile)
            ini.write_text('[ModLoading]\nmode = prepared_profile\ngpu_cache_enabled = 1\ngpu_cache_mb = 2048\nstartup_gpu_preload = 1\nstartup_preload_seconds = 30\n')
            self.assertEqual(baseline, capture.fingerprint(profile))
            ini.write_text(ini.read_text().replace('seconds = 30', 'seconds = 60'))
            self.assertNotEqual(baseline, capture.fingerprint(profile))

    def test_preload_policy_is_part_of_comparison_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary); (profile / 'ZZMI').mkdir()
            (profile / 'ZZMI/d3dx.ini').write_text('[ModLoading]\nmode = off\n')
            path = profile / 'PRELOAD-RESULT.json'
            path.write_text(json.dumps(dict(policy='model-file-cache-v1', enabled=True, bytes_read=123)))
            before = capture.fingerprint(profile)
            path.write_text(json.dumps(dict(policy='model-file-cache-v1', enabled=True, bytes_read=456)))
            self.assertEqual(before, capture.fingerprint(profile))
            path.write_text(json.dumps(dict(policy='model-file-cache-v1', enabled=False)))
            self.assertNotEqual(before, capture.fingerprint(profile))

    def test_shader_cache_hits_are_counted_separately(self):
        rows = ROWS + 'shader_cache_hit,instant,1,35000000,0,3,3,1,12,256,0\n'
        parsed = capture.parse_trace(HEADER + rows + FOOTER.replace('=4;', '=5;'))
        self.assertTrue(parsed['complete_lossless'])
        self.assertEqual(parsed['shader_cache_hits'], 1)
        self.assertNotIn('shader_cache_hit', parsed['stages'])

    def test_intervals_and_loss_accounting(self):
        parsed = capture.parse_trace(HEADER + ROWS + FOOTER)
        self.assertTrue(parsed['complete_lossless'])
        self.assertEqual(parsed['present_intervals']['median_ms'], 16.5)
        self.assertEqual(parsed['stages']['config_reload']['median_ms'], .5)
        for bad in [FOOTER.replace('written=4', 'written=5'), FOOTER.replace('dropped_full=0', 'dropped_full=1'), '']:
            self.assertFalse(capture.parse_trace(HEADER + ROWS + bad)['complete_lossless'])

    def test_gapped_frames_are_not_intervals(self):
        parsed = capture.parse_trace(HEADER + ROWS.replace('34000000,1000,2,3', '34000000,1000,3,4') + FOOTER)
        self.assertEqual(parsed['present_intervals']['count'], 1)
        self.assertEqual(parsed['present_frame_gaps'], 1)

    def test_comparison_rejects_missing_or_changed_evidence(self):
        # Caller sets TEMP/TMP to an owned repository scratch path.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            run = dict(status='complete', dll={'verified': True}, before={'identity': 'same'},
                       after={'identity': 'same'}, expected_dll='abc', trace=capture.parse_trace(HEADER + ROWS + FOOTER))
            for mode in ['baseline', 'optimized']: capture.write_json(directory / (mode + '.json'), dict(run, mode=mode))
            capture.compare(directory)
            self.assertTrue(json.loads((directory / 'comparison.json').read_text())['matched_capture_pair'])
            capture.write_json(directory / 'optimized.json', dict(run, mode='optimized', after={'identity': 'changed'}))
            capture.compare(directory)
            self.assertFalse(json.loads((directory / 'comparison.json').read_text())['matched_capture_pair'])

    @unittest.skipUnless(os.name == 'nt', 'Windows module inspection')
    def test_inspect_own_process_module(self):
        import ctypes
        dll = Path(os.environ['SystemRoot']) / 'System32/d3d11.dll'
        module = ctypes.WinDLL(str(dll))
        self.assertTrue(module._handle)
        self.assertTrue(capture.inspect_dll(os.getpid(), dll, capture.digest(dll))['verified'])
        self.assertFalse(capture.inspect_dll(os.getpid(), dll, '0' * 64)['verified'])

    @unittest.skipUnless(os.name == 'nt', 'Windows observer filesystem')
    def test_passive_observer_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); profile = root / '.collector-data/collector-auto-20261023/diagnostic-launcher'
            native = profile / 'ZZMI'; native.mkdir(parents=True)
            previous = root / 'captures/loading-speed/baseline.json'
            previous.parent.mkdir(parents=True); previous.write_text('previous completed capture')
            (native / 'd3dx.ini').write_text('[ModLoading]\nmode = off\n')
            trace = native / 'mod-load-trace-123-1-1.csv'; trace.write_text(HEADER + ROWS + FOOTER)
            args = SimpleNamespace(root=root, mode='baseline', dll_hash='abc', since=time.time() - 10, ready=str(profile / 'ready.json'))
            with mock.patch.object(capture, 'inspect_dll', return_value={'verified': True, 'pid': 123}), mock.patch.object(capture.ctypes.windll.user32, 'MessageBoxW', return_value=1):
                capture.capture(args)
            report = json.loads((root / 'captures/loading-speed/abc/baseline.json').read_text())
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['before'], report['after'])
            self.assertFalse(trace.exists())
            self.assertTrue((root / 'captures/loading-speed/abc/baseline.csv').exists())
            self.assertEqual(previous.read_text(), 'previous completed capture')


if __name__ == '__main__': unittest.main()
