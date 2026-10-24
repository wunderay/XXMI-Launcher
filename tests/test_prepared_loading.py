"""Run with PYTHONPATH=src/xxmi_launcher python -m unittest discover -s tests."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from core.prepared_loading import (
    CAPABILITY_FILE, LIMITS, MODES, PreparedLoadingSettings, PreparationCapability,
    apply_preparation_settings, inspect_capability, validate_limit,
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


if __name__ == '__main__':
    unittest.main()
