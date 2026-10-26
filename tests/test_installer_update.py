"""Updater decisions with real launcher/process-manager code and inert Windows transport.

Run on Windows with PYTHONPATH=src/xxmi_launcher python -m unittest discover
-s tests -p test_installer_update.py -v. No installer, UAC, network or game runs.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ProcessFixture:
    def __init__(self, pid, name='msiexec.exe', created=1):
        self.pid, self._name, self.created = pid, name, created
        self.alive = True
        self.descendants = []

    def is_running(self):
        return self.alive

    def create_time(self):
        return self.created

    def name(self):
        return self._name

    def children(self, recursive=False):
        return list(self.descendants)


@unittest.skipUnless(sys.platform == 'win32', 'Real launcher imports require Windows')
class InstallerUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import core.locale_manager as Locale
        Locale.initialize(Path(__file__).resolve().parents[1])
        Locale.Locale.set_active_locale('EN', save_to_file=False)
        # Mirror normal bootstrap order to resolve Config's package imports.
        import core.config_manager
        import core.packages.launcher_package as launcher
        import core.utils.process_manager as processes
        cls.launcher, cls.processes = launcher, processes

    def setUp(self):
        self.package = self.launcher.LauncherPackage.__new__(self.launcher.LauncherPackage)
        self.package.downloaded_asset_path = Path('C:/fixture/update package.msi')
        self.package.cfg = SimpleNamespace(latest_version='2.4.10')
        self.root = ProcessFixture(101)
        self.native = SimpleNamespace(pid=101, _handle=1, poll=lambda: self.exit_code)
        self.exit_code = None
        self.visible_pids = set()
        self.clock = 0
        self.on_tick = lambda: None
        self.statuses = []
        events = self.launcher.Events
        subscription = events.Subscribe(events.Application.StatusUpdate,
                                        lambda event: self.statuses.append(str(event.status)))
        self.addCleanup(events.Unsubscribe, callback_id=subscription)

        def start_patch(target, name, **kwargs):
            mock = patch.object(target, name, **kwargs)
            value = mock.start()
            self.addCleanup(mock.stop)
            return value

        self.popen = start_patch(self.processes.subprocess, 'Popen', return_value=self.native)
        self.process_lookup = start_patch(self.processes.psutil, 'Process', return_value=self.root)
        start_patch(self.processes.psutil, 'process_iter',
                    side_effect=AssertionError('Updater must never search by global process name'))
        start_patch(self.processes.time, 'monotonic', side_effect=lambda: self.clock)

        def tick(seconds):
            self.clock += seconds
            self.on_tick()

        start_patch(self.processes.time, 'sleep', side_effect=tick)
        start_patch(self.processes.win32gui, 'EnumWindows',
                    side_effect=lambda callback, result: [callback(pid, result) for pid in self.visible_pids])
        start_patch(self.processes.win32gui, 'IsWindowVisible', return_value=True)
        start_patch(self.processes.win32gui, 'IsIconic', return_value=False)
        start_patch(self.processes.win32process, 'GetWindowThreadProcessId',
                    side_effect=lambda hwnd: (1, hwnd))

    def test_standard_installer_owns_visible_window_and_preserves_arguments(self):
        self.visible_pids.add(self.root.pid)
        self.package.run_msi_installer()
        args, kwargs = self.popen.call_args
        command = args[0]
        executable = kwargs['executable']
        self.assertTrue(Path(executable).is_absolute())
        self.assertEqual(Path(executable).name.lower(), 'msiexec.exe')
        self.assertEqual(command, (
            f'"{executable}" /i "{self.package.downloaded_asset_path}" /qr /norestart '
            f'APPDIR="{self.launcher.Paths.App.Root}" CREATE_SHORTCUTS=""'
        ))
        self.assertIs(kwargs['shell'], False)
        self.popen.assert_called_once()

    def test_msi_raw_arguments_preserve_quotes_and_shell_characters(self):
        self.package.downloaded_asset_path = Path('C:/fixture/update package & retained.msi')
        self.visible_pids.add(self.root.pid)
        self.package.run_msi_installer()
        command = self.popen.call_args.args[0]
        self.assertIsInstance(command, str)
        self.assertIn(f'/i "{self.package.downloaded_asset_path}"', command)
        self.assertTrue(command.endswith('CREATE_SHORTCUTS=""'))
        self.assertIs(self.popen.call_args.kwargs['shell'], False)

    def test_default_executable_launch_still_uses_argument_list(self):
        target = self.processes.ExecutableLaunch(
            exe_path=Path('C:/fixture/ordinary application.exe'),
            cmd_args='"argument with spaces" ordinary',
        )
        context = self.processes.LaunchContext(
            start_method=self.launcher.StartMethod.NATIVE,
            process_name=target.exe_path.name,
            target=target,
        )
        self.processes.ProcessManager(context)._launch(context)
        self.assertEqual(self.popen.call_args.args[0],
                         [str(target.exe_path), 'argument with spaces', 'ordinary'])
        self.assertNotIn('executable', self.popen.call_args.kwargs)
        self.assertNotIn('shell', self.popen.call_args.kwargs)

    def test_raw_arguments_reject_parent_override_without_launching(self):
        context = self.processes.LaunchContext(
            start_method=self.launcher.StartMethod.NATIVE,
            process_name='msiexec.exe',
            target=self.processes.ExecutableLaunch(
                exe_path=Path('C:/Windows/System32/msiexec.exe'),
                cmd_args='CREATE_SHORTCUTS=""',
                preserve_arguments=True,
            ),
            parent_process=object(),
        )
        with self.assertRaisesRegex(ValueError, 'preserve_arguments does not support parent_process'):
            self.processes.ProcessManager(context)._launch(context)
        self.popen.assert_not_called()

    def test_legacy_enhanced_ui_window_is_an_owned_descendant(self):
        child = ProcessFixture(102, 'EnhancedUI.exe')
        self.root.descendants = [child]
        self.visible_pids.add(child.pid)
        self.package.run_msi_installer()

    def test_standard_installer_descendant_window_is_accepted(self):
        child = ProcessFixture(102, 'MSIEXEC.EXE')
        self.root.descendants = [child]
        self.visible_pids.add(child.pid)
        self.package.run_msi_installer()

    def test_unrelated_installer_windows_do_not_prove_startup(self):
        self.visible_pids.update((999, 1000))
        with self.assertRaisesRegex(ValueError, 'Failed to start'):
            self.package.run_msi_installer()
        self.assertEqual(self.clock, 15)
        self.popen.assert_called_once()

    def test_other_descendant_executable_window_does_not_prove_startup(self):
        child = ProcessFixture(102, 'cmd.exe')
        self.root.descendants = [child]
        self.visible_pids.add(child.pid)
        with self.assertRaisesRegex(ValueError, 'Failed to start'):
            self.package.run_msi_installer()

    def test_observed_descendant_retains_identity_after_ancestor_disappears(self):
        child = ProcessFixture(102, 'EnhancedUI.exe')
        self.root.descendants = [child]

        def hand_off():
            self.root.alive = False
            self.visible_pids.add(child.pid)

        self.on_tick = hand_off
        self.package.run_msi_installer()

    def test_reused_descendant_pid_does_not_prove_startup(self):
        child = ProcessFixture(102, 'EnhancedUI.exe')
        self.root.descendants = [child]

        def reuse_pid():
            self.root.descendants = []
            child.created = 2
            self.visible_pids.add(child.pid)

        self.on_tick = reuse_pid
        with self.assertRaisesRegex(ValueError, 'Failed to start'):
            self.package.run_msi_installer()

    def test_fast_successful_exit_requires_no_window(self):
        self.exit_code = 0
        self.process_lookup.side_effect = self.processes.psutil.NoSuchProcess(101)
        self.package.run_msi_installer()
        self.assertEqual(self.clock, 0)

    def test_restart_success_codes_are_reported_without_restart(self):
        for code in (1641, 3010):
            with self.subTest(code=code):
                self.exit_code = code
                self.package.run_msi_installer()
                self.assertIn('Windows restart required', self.statuses[-1])
        self.assertEqual(self.popen.call_count, 2)

    def test_windows_installer_errors_remain_errors_even_with_visible_window(self):
        self.visible_pids.add(self.root.pid)
        for code in (1602, 1603, 1618):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, str(code)):
                self.exit_code = code
                self.package.run_msi_installer()

    def test_process_launch_errors_propagate_without_elevation_retry(self):
        self.popen.side_effect = PermissionError('fixture launch denied')
        with self.assertRaisesRegex(PermissionError, 'fixture launch denied'):
            self.package.run_msi_installer()
        self.popen.assert_called_once()

    def test_numeric_version_ordering_and_developer_guard(self):
        for installed, latest, available in (
            ('2.4.9', '2.4.10', True), ('2.4.10', '2.4.9', False),
            ('2.4.10', '2.4.10', False), ('2.9.9', '2.10.0', True),
            ('2.99.99', '3.0.0', True), ('0.0.0', '2.4.10', False),
        ):
            with self.subTest(installed=installed, latest=latest):
                self.package.cfg.latest_version = latest
                with patch.object(self.package, 'get_installed_version', return_value=installed):
                    self.assertEqual(self.package.update_available(), available)

    def test_release_metadata_finds_multi_digit_patch_installer_asset(self):
        from core.utils.github_client import GitHubClient
        import core.utils.github_client as github

        with patch.object(self.launcher.winreg, 'OpenKey', side_effect=FileNotFoundError):
            package = self.launcher.LauncherPackage()
        self.addCleanup(package.unsubscribe)
        response = dict(tag_name='v2.4.10', body='## Signature\n- AAAA\n', assets=[dict(
            name='XXMI-Launcher-Installer-Online-v2.4.10.msi',
            browser_download_url='https://example.invalid/inert-update.msi',
        )])
        with patch.object(github.requests, 'get', return_value=SimpleNamespace(json=lambda: response)):
            version, url, signature, _, _ = GitHubClient().fetch_latest_release(
                repo_owner=package.metadata.github_repo_owner,
                repo_name=package.metadata.github_repo_name,
                asset_version_pattern=package.asset_version_pattern,
                asset_name_format=package.metadata.asset_name_format,
                signature_pattern=package.signature_pattern,
            )
        self.assertEqual(version, '2.4.10')
        self.assertEqual(url, 'https://example.invalid/inert-update.msi')
        self.assertEqual(signature, 'AAAA')

    def test_missing_and_invalid_versions_fail_closed(self):
        for installed, latest in (
            ('2.4.9', ''), ('2.4.9', None), ('', '2.4.10'),
            ('2.4', '2.4.10'), ('2.4.9', 'invalid'), ('2.4.9', '2.4.10.1'),
            ('2.4.9', '2.4.10-preview'), ('2.4.9', '2.4.-1'),
            ('2.4.9', '2.4.10\n'), ('2.4.9', '2.4.' + '9' * 5000),
        ):
            with self.subTest(installed=installed, latest=str(latest)[:50]):
                self.package.cfg.latest_version = latest
                with patch.object(self.package, 'get_installed_version', return_value=installed):
                    self.assertFalse(self.package.update_available())


if __name__ == '__main__':
    unittest.main()
