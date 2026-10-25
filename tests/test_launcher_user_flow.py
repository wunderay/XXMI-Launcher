"""Windows GUI integration using the normal launcher's settings and update buttons.

Run with PYTHONPATH=src/xxmi_launcher python -m unittest discover -s tests
-p test_launcher_user_flow.py -v. Publisher verification is an explicit inert
fixture callback, not evidence of publisher acceptance or live qualification.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'win32', 'Real launcher GUI requires Windows')
class LauncherUserFlowTests(unittest.TestCase):
    def setUp(self):
        # Keep Windows dependencies out of Linux discovery, including transitive
        # Win32 imports from the production package and GUI modules.
        import customtkinter as ctk
        import core.config_manager as Config
        import core.event_manager as Events
        import core.path_manager as Paths
        import gui.vars as Vars
        from core.game_launcher import Launcher
        from core.packages.migoto_package import MigotoPackage
        from gui.classes.containers import UIFrame
        from gui.classes.tooltip import UIToolTipEngine

        self.Config, self.Events, self.Paths, self.Vars = Config, Events, Paths, Vars
        self.temp = tempfile.TemporaryDirectory(prefix='xxmi-launcher-gui-')
        self.addCleanup(self.temp.cleanup)
        self.root_path = Path(self.temp.name)

        def replace(obj, name, value):
            missing = object()
            previous = getattr(obj, name, missing)
            def restore():
                if previous is missing:
                    delattr(obj, name)
                else:
                    setattr(obj, name, previous)
            self.addCleanup(restore)
            setattr(obj, name, value)

        replace(Events, 'events', {})
        app_paths = Paths.Paths()
        for name in ('Root', 'Resources', 'Themes', 'Backups', 'Locale'):
            path = self.root_path if name == 'Root' else self.root_path / name
            path.mkdir(parents=True, exist_ok=True)
            setattr(app_paths, name, path)
        replace(Paths, 'App', app_paths)

        # Use repository images and theme through temporary launcher paths.
        # No installed launcher, desktop, user config or game path is consulted.
        theme_source = Path(__file__).resolve().parents[1] / 'Themes' / 'Default'
        for relative in (
            'custom-tkinter-theme.json',
            'MainWindow/LauncherFrame/SettingsFrame/info.webp',
            'MainWindow/LauncherFrame/button-update.png',
            'MainWindow/LauncherFrame/ToolBarFrame/button-tool-background.png',
            'MainWindow/LauncherFrame/ToolBarFrame/button-tool-check-for-updates.png',
        ):
            destination = app_paths.Themes / 'Default' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(theme_source / relative, destination)
        replace(ctk.ThemeManager, 'theme', dict(ctk.ThemeManager.theme))
        ctk.set_default_color_theme(str(app_paths.Themes / 'Default/custom-tkinter-theme.json'))

        cfg = Config.AppConfig()
        cfg.Launcher.active_importer = 'GIMI'
        cfg.Active = cfg.Importers.GIMI
        cfg.Active.Importer.importer_folder = str(self.root_path / 'Importer')
        cfg.Active.Importer.importer_path.mkdir()
        replace(Config, 'Config', cfg)
        replace(Config, 'Manager', Config.ConfigManager(cfg))
        for name, value in (('Launcher', cfg.Launcher), ('Packages', cfg.Packages),
                            ('Importers', cfg.Importers), ('Active', cfg.Active)):
            replace(Config, name, value)

        self.gui = ctk.CTk()
        self.gui_destroyed = False
        self.addCleanup(self.destroy_gui)
        self.gui.withdraw()
        self.gui.cfg = SimpleNamespace(width=1280, height=720)
        self.gui.get_resource_path = lambda: Path('MainWindow/LauncherFrame')
        self.gui.tooltip_engine = UIToolTipEngine({'master': self.gui})
        self.callback_errors = []
        self.gui.report_callback_exception = lambda *error: self.callback_errors.append(error)
        replace(Vars, 'Settings', Vars.AppSettings())
        for name in ('Launcher', 'Packages', 'Importers', 'Active'):
            replace(Vars, name, None)
        Vars.Settings.initialize(Config.Config, self.gui)
        Vars.Settings.load()
        self.host = UIFrame(self.gui, width=1000, height=600)
        self.host.pack()

        self.package = MigotoPackage()
        replace(Launcher, 'xxmi_injector', self.package)
        replace(Launcher, 'xxmi_dll_identity', None)
        self.package.package_path.mkdir(parents=True)
        # Mock only the publisher verification boundary. Real capability loading
        # still parses the real manifest and binds its metadata to deployed bytes.
        verifier = patch.object(self.package.security, 'verify', side_effect=lambda signature, data:
            signature == self.fixture_signature(data))
        verifier.start()
        self.addCleanup(verifier.stop)
        self.write_package()
        # Destroy production widgets while their Vars/Config globals still exist.
        # The earlier idempotent registration also covers partial setup failures.
        self.addCleanup(self.destroy_gui)

    def destroy_gui(self):
        if not self.gui_destroyed:
            self.gui_destroyed = True
            for callback in self.gui.tk.call('after', 'info'):
                self.gui.after_cancel(callback)
            self.gui.destroy()

    @staticmethod
    def fixture_signature(data):
        return 'explicit fixture authentication ' + hashlib.sha256(data).hexdigest()

    def write_package(self, authenticated=True):
        from core.prepared_loading import CAPABILITY_FILE, V2_FEATURES
        dll = self.Config.Active.Importer.importer_path / 'd3d11.dll'
        dll.write_bytes(b'Inert GUI fixture DLL; never loaded or executed')
        marker = self.package.package_path / CAPABILITY_FILE
        marker.write_text(json.dumps(dict(
            schema_version=1, mod_loading_version=2,
            modes=['off', 'adaptive', 'prepared_profile'], features=list(V2_FEATURES),
            dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),
        )), encoding='utf-8')
        signatures = {marker.name: self.fixture_signature(marker.read_bytes())} if authenticated else {}
        (self.package.package_path / 'Manifest.json').write_text(
            json.dumps(dict(version='gui-fixture', signatures=signatures)), encoding='utf-8')

    def open_importer_settings(self):
        from gui.windows.settings.frames.importer_settings_frame import ModelImporterSettingsFrame
        from gui.windows.settings.settings_content_frame import SettingsSectionFrame
        frame = self.host.put(ModelImporterSettingsFrame(self.host))
        frame.grid(row=0, column=0, sticky='news')
        frame.show()
        sections = [section for section in frame._sections
                    if any(not option.load_if or option.load_if.predicate() for option in section.options)]
        options = [[option for option in section.options
                    if not option.load_if or option.load_if.predicate()] for section in sections]
        done = []
        frame.render_sections_async(done_callback=lambda: done.append(True))
        deadline = time.monotonic() + 15
        while not done and time.monotonic() < deadline:
            self.gui.update()
            if self.callback_errors:
                _, error, traceback = self.callback_errors[0]
                raise error.with_traceback(traceback)
            time.sleep(0.005)
        self.assertTrue(done, 'Production settings async rendering did not complete within 15 seconds')
        section_frames = [element for element in frame.elements.values()
                          if isinstance(element, SettingsSectionFrame)]
        self.assertEqual(len(section_frames), len(options))
        rows = {}
        for section, specs in zip(section_frames, options):
            self.assertEqual(len(section._option_frames), len(specs))
            for row, spec in zip(section._option_frames, specs):
                if spec.value_variable:
                    rows[spec.value_variable] = row
                elif spec.button_text:
                    rows[spec.button_text] = row
        return frame, rows

    def test_authenticated_package_controls_select_validate_and_apply_normal_settings(self):
        from core.prepared_loading import apply_preparation_settings
        from core.utils.ini_handler import IniHandler, IniHandlerSettings
        from gui.windows.settings.settings_content_frame import (
            OptionWidgetErrorLabel, OptionWidgetValueDropdown, OptionWidgetValueInputInteger,
        )
        frame, rows = self.open_importer_settings()
        mode = rows['Vars.Active.Migoto.preparation_mode'].grab(OptionWidgetValueDropdown)
        self.assertEqual(mode.cget('state'), 'normal')
        self.assertIn('prepared_profile', mode._values_dict)
        mode._dropdown_callback(mode._values_dict['prepared_profile'])
        self.assertEqual(self.Vars.Active.Migoto.preparation_mode.get(), 'prepared_profile')
        self.assertEqual(frame.validate_preparation_mode(), '')

        row = rows['Vars.Active.Migoto.preparation_cpu_cache_mb']
        entry = row.grab(OptionWidgetValueInputInteger)
        entry.delete(0, 'end')
        entry.insert(0, '-1')
        self.gui.update()
        error = row.grab(OptionWidgetErrorLabel)
        self.assertTrue(error.cget('text'))
        self.assertTrue(error.winfo_manager())
        self.Vars.Settings.save_vars(self.Vars.Settings, self.Config.Config)
        ini = IniHandler(IniHandlerSettings(ignore_comments=False), '; user comment\n[User]\nvalue = keep\n')
        before = ini.to_string()
        with self.assertRaises(ValueError):
            apply_preparation_settings(ini, self.Config.Active.Migoto, self.package.preparation_capability())
        self.assertEqual(ini.to_string(), before)

        entry.delete(0, 'end')
        entry.insert(0, '1024')
        self.gui.update()
        self.assertEqual(frame.validate_preparation_limit('cpu_cache_mb'), '')
        self.assertFalse(error.winfo_manager())
        self.Vars.Settings.save_vars(self.Vars.Settings, self.Config.Config)
        apply_preparation_settings(ini, self.Config.Active.Migoto, self.package.preparation_capability())
        restored = IniHandler(IniHandlerSettings(ignore_comments=False), ini.to_string())
        self.assertEqual(restored.get_section('ModLoading').get_option('mode'), 'prepared_profile')
        self.assertEqual(restored.get_section('ModLoading').get_option('cpu_cache_mb'), '1024')
        self.assertEqual(restored.get_section('User').get_option('value'), 'keep')
        self.assertIn('; user comment', restored.to_string())

        self.Vars.Active.Migoto.manage_xxmi_dll_config.set(False)
        self.gui.update()
        self.assertEqual(mode.cget('state'), 'disabled')

    def test_unsigned_package_disables_preparation_and_explains_why(self):
        from gui.windows.settings.settings_content_frame import OptionWidgetValueButton, OptionWidgetValueDropdown
        self.write_package(authenticated=False)
        frame, rows = self.open_importer_settings()
        mode = rows['Vars.Active.Migoto.preparation_mode'].grab(OptionWidgetValueDropdown)
        self.assertFalse(frame.can_prepare())
        self.assertEqual(mode.cget('state'), 'disabled')
        self.assertIn('missing signature', frame._preparation_capability.reason)
        messages = []
        self.Events.Subscribe(self.Events.Application.ShowInfo, messages.append, caller_id=self)
        rows['Diagnostics'].grab(OptionWidgetValueButton).invoke()
        self.assertEqual(len(messages), 1)
        self.assertIn(frame._preparation_capability.reason, messages[0].message)

    def test_normal_update_buttons_dispatch_standard_application_events(self):
        from gui.classes.containers import UIFrame
        from gui.windows.main.launcher_frame.launcher_frame import UpdateButton
        from gui.windows.main.launcher_frame.tool_bar import CheckForUpdatesButton
        check_events, update_events = [], []
        self.Events.Subscribe(self.Events.Application.CheckForUpdates, check_events.append, caller_id=self)
        self.Events.Subscribe(self.Events.Application.Update, update_events.append, caller_id=self)
        self.host.resource_override = Path('MainWindow/LauncherFrame')
        update = self.host.put(UpdateButton(self.host))
        toolbar = self.host.put(UIFrame(self.host))
        toolbar.resource_override = Path('MainWindow/LauncherFrame/ToolBarFrame')
        toolbar.locked = False
        check = toolbar.put(CheckForUpdatesButton(toolbar))
        for button in (check, update):
            button.hovered = True
            button._handle_button_release(None)
        self.assertEqual(len(check_events), 1)
        self.assertEqual(len(update_events), 1)
        self.assertTrue(update_events[0].force)
        self.assertEqual(self.package.downloaded_asset_path, None)


if __name__ == '__main__':
    unittest.main()
