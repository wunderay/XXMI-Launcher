"""Run with PYTHONPATH=src/xxmi_launcher python -m unittest discover -s tests."""

import unittest
from dataclasses import fields

from core.prepared_loading import (
    BACKGROUND_RELOAD, MODES, V2_FEATURES, PreparedLoadingSettings, PreparationCapability,
    apply_preparation_settings, reconcile_preparation_settings,
)
from core.utils.ini_handler import IniHandler, IniHandlerSettings


def ini(text='[Logging]\nshow_warnings = 0\n'):
    return IniHandler(IniHandlerSettings(ignore_comments=False), text)


def capability(features=(*V2_FEATURES, BACKGROUND_RELOAD), version=2, supported=True):
    return PreparationCapability(supported, 'background reload fixture', 'a' * 64, tuple(MODES),
                                 version, tuple(features))


def enabled_settings(**overrides):
    values = dict(preparation_mode='adaptive', preparation_transactional_activation=True,
                  preparation_general_fast_refresh=True, preparation_background_reload=True)
    values.update(overrides)
    return PreparedLoadingSettings(**values)


def option(handler):
    section = handler.get_section('ModLoading')
    return None if section is None else section.get_option(BACKGROUND_RELOAD)


class BackgroundReloadSettingsTests(unittest.TestCase):
    def test_default_is_off_and_never_written(self):
        self.assertFalse(PreparedLoadingSettings().preparation_background_reload)
        self.assertIn('preparation_background_reload', [item.name for item in fields(PreparedLoadingSettings)])
        handler = ini()
        apply_preparation_settings(handler, PreparedLoadingSettings(preparation_mode='adaptive'), capability())
        self.assertIsNone(option(handler))

    def test_enabled_with_declared_support_writes_one_and_roundtrips(self):
        handler = ini()
        apply_preparation_settings(handler, enabled_settings(), capability())
        self.assertEqual(option(ini(handler.to_string())), '1')
        self.assertEqual(handler.get_section('ModLoading').get_option('general_fast_refresh'), '1')

    def test_undeclared_or_unsupported_library_never_gets_option(self):
        for fixture in (capability(features=V2_FEATURES), capability(features=(BACKGROUND_RELOAD,), version=1),
                        capability(supported=False), PreparationCapability(False, 'XXMI package is unavailable.')):
            with self.subTest(capability=fixture):
                handler = ini()
                apply_preparation_settings(handler, enabled_settings(), fixture)
                self.assertIsNone(option(handler))

    def test_requires_general_and_transactional_refresh(self):
        for overrides in ({'preparation_general_fast_refresh': False},
                          {'preparation_transactional_activation': False},
                          {'preparation_mode': 'off'}):
            with self.subTest(overrides=overrides):
                handler = ini()
                apply_preparation_settings(handler, enabled_settings(**overrides), capability())
                self.assertIsNone(option(handler))

    def test_disabling_or_losing_support_turns_leftover_option_off(self):
        for settings, fixture in ((enabled_settings(preparation_background_reload=False), capability()),
                                  (enabled_settings(), capability(features=V2_FEATURES)),
                                  (enabled_settings(preparation_mode='off'), capability()),
                                  (enabled_settings(), PreparationCapability(False, 'Unsigned package'))):
            with self.subTest(settings=settings, capability=fixture):
                handler = ini('[ModLoading]\nmode = adaptive\nbackground_reload = 1\n')
                apply_preparation_settings(handler, settings, fixture)
                self.assertEqual(option(handler), '0')

    def test_non_boolean_fails_before_ini_mutation(self):
        handler = ini()
        before = handler.to_string()
        with self.assertRaisesRegex(ValueError, BACKGROUND_RELOAD):
            apply_preparation_settings(handler, enabled_settings(preparation_background_reload=1), capability())
        self.assertEqual(handler.to_string(), before)

    def test_reconcile_discards_preference_without_declared_support(self):
        for fixture, expected in ((capability(), True), (capability(features=V2_FEATURES), False),
                                  (capability(features=(BACKGROUND_RELOAD,), version=1), False),
                                  (capability(supported=False), False)):
            with self.subTest(capability=fixture):
                settings = enabled_settings()
                reconcile_preparation_settings(settings, fixture)
                self.assertIs(settings.preparation_background_reload, expected)
        settings = enabled_settings(preparation_background_reload='yes')
        reconcile_preparation_settings(settings, capability())
        self.assertIs(settings.preparation_background_reload, False)


if __name__ == '__main__':
    unittest.main()
