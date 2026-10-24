"""Run with PYTHONPATH=src/xxmi_launcher python -m unittest discover -s tests."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from core.prepared_loading import (
    CAPABILITY_FILE, LIMITS, MODES, PreparedLoadingSettings, PreparationCapability,
    apply_preparation_settings, inspect_capability, validate_limit, read_preparation_status,
)
from core.utils.ini_handler import IniHandler, IniHandlerSettings
from core.utils.security import Security


class PreparedLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dll = self.root / 'd3d11.dll'
        self.dll.write_bytes(b'test DLL fixture; never executed')
        self.marker = self.root / CAPABILITY_FILE
        self.security = Security()
        self.security.generate_key_pair()  # Isolated fixture key, never a package trust override.
        self.signatures = {}

    def write_marker(self, **overrides):
        data = dict(schema_version=1, mod_loading_version=1,
                    dll_sha256=hashlib.sha256(self.dll.read_bytes()).hexdigest(), modes=list(MODES))
        data.update(overrides)
        self.marker.write_bytes(json.dumps(data).encode())
        self.signatures[self.marker.name] = self.security.sign(self.marker.read_bytes())

    def inspect(self):
        return inspect_capability(self.dll, self.marker,
                                  lambda path, data: self.security.verify(self.signatures.get(path.name), data))

    @staticmethod
    def ini(text='[Logging]\nshow_warnings = 0\n'):
        return IniHandler(IniHandlerSettings(ignore_comments=False), text)

    def test_authenticated_metadata_is_bound_to_deployed_dll(self):
        self.write_marker()
        self.assertTrue(self.inspect().supported)
        self.dll.write_bytes(b'updated or third-party DLL')
        self.assertFalse(self.inspect().supported)

    def test_unsigned_or_tampered_marker_never_enables_controls(self):
        self.write_marker()
        self.signatures.clear()
        self.assertFalse(self.inspect().supported)
        self.write_marker()
        self.marker.write_bytes(self.marker.read_bytes() + b' ')
        self.assertFalse(self.inspect().supported)

    def test_unknown_versions_and_malformed_modes_fail_closed(self):
        for overrides in ({'schema_version': 2}, {'mod_loading_version': 0},
                          {'schema_version': True}, {'modes': ['off', 'future']},
                          {'modes': ['adaptive']}, {'modes': [{}]}):
            with self.subTest(overrides=overrides):
                self.write_marker(**overrides)
                self.assertFalse(self.inspect().supported)

    def test_missing_malformed_and_oversized_metadata_fail_closed(self):
        self.assertFalse(self.inspect().supported)
        for data in (b'{', b'[]', b'x' * 65537):
            self.marker.write_bytes(data)
            self.signatures[self.marker.name] = self.security.sign(data)
            self.assertFalse(self.inspect().supported)

    def test_old_configuration_defaults_and_json_settings_roundtrip(self):
        defaults = PreparedLoadingSettings(**{})
        self.assertEqual(defaults.preparation_mode, 'off')
        self.assertFalse(defaults.preparation_loading_window)
        configured = PreparedLoadingSettings(preparation_mode='adaptive', preparation_cpu_cache_mb=1024)
        restored = PreparedLoadingSettings(**json.loads(json.dumps(asdict(configured))))
        self.assertEqual(configured, restored)

    def test_limits_reject_negative_overflow_boolean_and_non_integer(self):
        for key, (lower, upper) in LIMITS.items():
            for value in (lower - 1, upper + 1, True, '512', 1.5):
                with self.subTest(key=key, value=value):
                    self.assertTrue(validate_limit(key, value))
            self.assertFalse(validate_limit(key, lower))
            self.assertFalse(validate_limit(key, upper))
        for key in ('cpu_cache_mb', 'gpu_cache_mb'):
            self.assertTrue(validate_limit(key, 15))
            self.assertFalse(validate_limit(key, 16))

    def test_legacy_dll_does_not_get_experimental_section(self):
        ini = self.ini()
        original = ini.to_string()
        apply_preparation_settings(ini, PreparedLoadingSettings(preparation_mode='adaptive'),
                                   PreparationCapability(False, 'Unsupported DLL'))
        self.assertEqual(ini.to_string(), original)

    def test_capability_loss_disables_stale_experimental_configuration(self):
        ini = self.ini('[ModLoading]\nmode = adaptive\nauto_activate = 1\npreparation_loading_window = 1\n')
        apply_preparation_settings(ini, PreparedLoadingSettings(), PreparationCapability(False, 'DLL updated'))
        section = ini.get_section('ModLoading')
        self.assertEqual(section.get_option('mode'), 'off')
        self.assertEqual(section.get_option('auto_activate'), '0')
        self.assertEqual(section.get_option('preparation_loading_window'), '0')

    def test_authenticated_configuration_survives_real_ini_roundtrip(self):
        self.write_marker()
        ini = self.ini('; preserve comments\n[Logging]\nshow_warnings = 0\n')
        settings = PreparedLoadingSettings(preparation_mode='prepared_profile', preparation_gpu_cache_mb=0)
        apply_preparation_settings(ini, settings, self.inspect())
        restored = self.ini(ini.to_string())
        section = restored.get_section('ModLoading')
        self.assertEqual(section.get_option('mode'), 'prepared_profile')
        self.assertEqual(section.get_option('cpu_cache_mb'), '512')
        self.assertEqual(section.get_option('gpu_cache_mb'), '0')
        self.assertEqual(section.get_option('auto_activate'), '0')
        self.assertEqual(section.get_option('preparation_loading_window'), '0')
        self.assertEqual(restored.get_section('Logging').get_option('show_warnings'), '0')
        self.assertIn('; preserve comments', restored.to_string())

    def test_invalid_limits_fail_before_any_ini_mutation(self):
        ini = self.ini()
        self.write_marker()
        before = ini.to_string()
        with self.assertRaises(ValueError):
            apply_preparation_settings(ini, PreparedLoadingSettings(preparation_mode='adaptive',
                                       preparation_gpu_cache_mb=-1), self.inspect())
        self.assertEqual(ini.to_string(), before)

    def test_explicit_refresh_and_disk_cache_are_opt_in_and_roundtrip(self):
        self.write_marker()
        ini = self.ini()
        settings = PreparedLoadingSettings(preparation_mode='adaptive', preparation_disk_cache_mb=128,
                                           preparation_refresh_key='ctrl shift no_alt VK_F8')
        apply_preparation_settings(ini, settings, self.inspect())
        section = self.ini(ini.to_string()).get_section('ModLoading')
        self.assertEqual(section.get_option('disk_cache_mb'), '128')
        self.assertEqual(section.get_option('refresh_key'), 'ctrl shift no_alt VK_F8')
        self.assertEqual(section.get_option('auto_activate'), '0')
        apply_preparation_settings(ini, PreparedLoadingSettings(), self.inspect())
        self.assertEqual(ini.get_section('ModLoading').get_option('disk_cache_mb'), '0')
        self.assertEqual(ini.get_section('ModLoading').get_option('refresh_key'), '')

    def test_invalid_refresh_shortcut_fails_before_ini_mutation(self):
        self.write_marker()
        ini = self.ini()
        before = ini.to_string()
        with self.assertRaisesRegex(ValueError, 'refresh_key'):
            apply_preparation_settings(ini, PreparedLoadingSettings(preparation_mode='adaptive',
                                       preparation_refresh_key='VK_F10\nmode = adaptive'), self.inspect())
        self.assertEqual(ini.to_string(), before)

    def test_off_remains_available_with_invalid_limits(self):
        self.write_marker()
        ini = self.ini()
        apply_preparation_settings(ini, PreparedLoadingSettings(preparation_cpu_cache_mb=-1), self.inspect())
        self.assertEqual(ini.get_section('ModLoading').get_option('mode'), 'off')

    def test_asset_admission_rejects_asset_larger_than_enabled_cpu_cache(self):
        self.write_marker()
        with self.assertRaisesRegex(ValueError, 'must not exceed'):
            apply_preparation_settings(self.ini(), PreparedLoadingSettings(preparation_mode='adaptive',
                                       preparation_cpu_cache_mb=16, preparation_max_asset_mb=64), self.inspect())

    def test_unsupported_mode_falls_back_to_off_and_status_never_fakes_ready(self):
        self.write_marker(modes=['off', 'adaptive'])
        capability = self.inspect()
        ini = self.ini()
        apply_preparation_settings(ini, PreparedLoadingSettings(preparation_mode='prepared_profile'), capability)
        self.assertEqual(ini.get_section('ModLoading').get_option('mode'), 'off')
        self.assertIn('Unknown / offline', capability.diagnostics)
        self.assertNotIn('status: Ready', capability.diagnostics)


class PreparationStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'status.json'
        self.capability = PreparationCapability(True, 'Authenticated fixture', 'a' * 64, tuple(MODES))
        self.now = 1000000

    def write(self, **overrides):
        report = dict(schema_version=1, process_id=123, dll_sha256='a' * 64,
                      updated_unix_ms=self.now, mode='prepared_profile', state='ready',
                      total_assets=3, cpu_ready=3, gpu_ready=3, compatibility_assets=0, reason='Prepared working set')
        report.update(overrides)
        self.path.write_text(json.dumps(report), encoding='utf-8')

    def read(self, capability=None, process_is_alive=None):
        return read_preparation_status(self.path, capability or self.capability, now_ms=self.now,
                                       process_is_alive=process_is_alive)

    def test_missing_report_is_offline(self):
        status = self.read()
        self.assertFalse(status.available)
        self.assertEqual(status.label, 'Unknown / offline')

    def test_unsigned_capability_never_reads_or_promotes_report(self):
        self.write()
        status = self.read(PreparationCapability(False, 'Unsigned package'))
        self.assertFalse(status.available)
        self.assertIn('authenticated', status.reason)

    def test_complete_prepared_profile_reports_ready_with_explicit_source(self):
        self.write()
        checked = []
        status = self.read(process_is_alive=lambda pid: checked.append(pid) or True)
        self.assertEqual(checked, [123])
        self.assertEqual(status.state, 'ready')
        self.assertEqual(status.label, 'Ready (reported)')
        self.assertIn('PID 123 (live process)', status.diagnostics)
        self.assertIn('not authentication or authorization', status.diagnostics)

    def test_unverified_pid_remains_explicitly_reported(self):
        self.write()
        self.assertIn('liveness unverified', self.read().diagnostics)

    def test_dead_process_cannot_report_ready(self):
        self.write()
        self.assertFalse(self.read(process_is_alive=lambda pid: False).available)

    def test_process_lookup_failure_is_offline(self):
        self.write()
        def denied(pid):
            raise RuntimeError('Lookup unavailable')
        self.assertFalse(self.read(process_is_alive=denied).available)

    def test_freshness_and_future_skew_boundaries(self):
        for offset, expected in ((-3001, False), (-3000, True), (1000, True), (1001, False)):
            with self.subTest(offset=offset):
                self.write(updated_unix_ms=self.now + offset)
                self.assertEqual(self.read().available, expected)

    def test_hash_schema_mode_and_state_must_match_contract(self):
        for override in ({'dll_sha256': 'b' * 64}, {'schema_version': 2}, {'schema_version': True},
                         {'state': 'invented'}, {'mode': 'invented'}, {'mode': 'off'},
                         {'state': 'off'}, {'process_id': 0}, {'process_id': 2**32}):
            with self.subTest(override=override):
                self.write(**override)
                self.assertFalse(self.read().available)

    def test_malformed_or_oversized_report_is_offline(self):
        for data in (b'{', b'[]', b'x' * 8193, b'[' * 1100 + b']' * 1100):
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                self.assertFalse(self.read().available)

    def test_count_types_ranges_and_consistency(self):
        for override in ({'cpu_ready': True}, {'total_assets': -1}, {'gpu_ready': 4},
                         {'cpu_ready': 4}, {'gpu_ready': 3, 'cpu_ready': 2},
                         {'compatibility_assets': 4}, {'total_assets': 2**63}):
            with self.subTest(override=override):
                self.write(state='preparing', **override)
                self.assertFalse(self.read().available)

    def test_ready_requires_complete_nonempty_prepared_profile_and_zero_compatibility(self):
        for override in ({'gpu_ready': 2}, {'cpu_ready': 2, 'gpu_ready': 2}, {'compatibility_assets': 1},
                         {'mode': 'adaptive'}, {'total_assets': 0, 'cpu_ready': 0, 'gpu_ready': 0}):
            with self.subTest(override=override):
                self.write(**override)
                self.assertFalse(self.read().available)

    def test_preparing_compatibility_failed_and_off_have_distinct_diagnostics(self):
        for state in ('preparing', 'compatibility', 'failed', 'off'):
            with self.subTest(state=state):
                self.write(state=state, mode='off' if state == 'off' else 'adaptive',
                           cpu_ready=2, gpu_ready=1, compatibility_assets=1, reason='Native diagnostic reason')
                status = self.read()
                self.assertTrue(status.available)
                self.assertEqual(status.state, state)
                self.assertIn('Native diagnostic reason', status.diagnostics)
                self.assertIn(status.label, self.capability.format_diagnostics(status))

    def test_reason_is_bounded_utf8_text_without_control_characters(self):
        for reason in ('x' * 257, '\u00e9' * 129, 'line\nbreak', None):
            self.write(reason=reason)
            self.assertFalse(self.read().available)
        self.write(reason='x' * 256)
        self.assertTrue(self.read().available)


if __name__ == '__main__':
    unittest.main()
