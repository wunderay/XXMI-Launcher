"""Ordinary launcher lifecycle, with platform services and publisher authentication mocked.

All bytes are inert fixtures under TemporaryDirectory; no real publisher key or game is used.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.prepared_loading import CAPABILITY_FILE, PreparedLoadingSettings, V2_FEATURES
from core.prepared_release import LIBRARIES, _StagedFile, _read_json, _write_json, _temporary


def fixture_signature(data):
    return 'mock publisher signature ' + hashlib.sha256(data).hexdigest()


class InstalledPackageTests(unittest.TestCase):
    def setUp(self, shared_root=None, shared_state=None):
        if shared_root is None:
            self.temp = tempfile.TemporaryDirectory()
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)
        else:
            self.root = Path(shared_root).resolve(strict=True)
            if self.root.parent != Path(tempfile.gettempdir()).resolve(strict=True):
                raise ValueError('Crash child may attach only to its task temporary directory')
        self.importer = self.root / 'importer'
        self.importer.mkdir(exist_ok=True)
        self.source = self.root / 'download'
        self.source.mkdir(exist_ok=True)
        security = types.SimpleNamespace(verify=lambda signature, data: signature == fixture_signature(data))
        root, source = self.root, self.source

        class Package:
            def __init__(self, metadata):
                self.metadata, self.security = metadata, security
                self.package_path = root / 'Packages' / 'XXMI'
                self.package_path.mkdir(parents=True, exist_ok=True)
                self.manifest = None
            def load_manifest(self):
                self.manifest = json.loads((self.package_path / 'Manifest.json').read_bytes())
            def get_signature(self, path):
                if self.manifest is None:
                    self.load_manifest()
                return self.manifest['signatures'][path.name]
            def validate_files(self, paths):
                for path in paths:
                    if not self.security.verify(self.get_signature(path), path.read_bytes()):
                        raise ValueError('invalid deployed fixture signature')
            def download_latest_version(self):
                self.downloaded_asset_path = source
                # Match Package.download_latest_version: replaces Manifest before install.
                (self.package_path / 'Manifest.json').write_bytes((source / 'Manifest.json').read_bytes())
            def move_contents(self, source, destination):
                shutil.copytree(source, destination, dirs_exist_ok=True)
            def verify_files_integrity(self, destination):
                pass  # Archive hash verification belongs to Package, outside this fixture.
            def update(self, clean=False):
                self.download_latest_version()
                try:
                    self.install_latest_version(clean)
                except Exception:
                    (self.package_path / 'Manifest.json').unlink(missing_ok=True)
                    raise
            def update_available(self):
                return True

        def module(name, **values):
            value = types.ModuleType(name)
            value.__dict__.update(values)
            return value
        self.config = module('core.config_manager', Active=types.SimpleNamespace(
            Importer=types.SimpleNamespace(importer_path=self.importer, deployed_migoto_signatures={},
                process_exe_names=['fixture-launch.exe'], game_exe_names=['fixture-game.exe'])))
        stubs = {
            'core.error_manager': module('core.error_manager'),
            'core.path_manager': module('core.path_manager',
                verify_path=lambda path: path.mkdir(parents=True, exist_ok=True),
                App=types.SimpleNamespace(is_av_error=lambda error: False)),
            'core.event_manager': MagicMock(), 'core.config_manager': self.config,
            'core.locale_manager': module('core.locale_manager', L=lambda key, text: text),
            'core.package_manager': module('core.package_manager', Package=Package,
                PackageMetadata=lambda **kw: types.SimpleNamespace(**kw)),
            'core.config.enums': module('core.config.enums',
                InputDisableMode=types.SimpleNamespace(MODS=0), LogLevel=types.SimpleNamespace(DISABLED=0)),
            'core.utils.dll_injector': module('core.utils.dll_injector', DllInjector=MagicMock()),
            'core.utils.process_manager': module('core.utils.process_manager', LaunchContext=object),
        }
        name = '_ordinary_migoto_fixture'
        path = Path(__file__).parents[1] / 'src/xxmi_launcher/core/packages/migoto_package.py'
        spec = importlib.util.spec_from_file_location(name, path)
        self.loaded = importlib.util.module_from_spec(spec)
        modules = patch.dict(sys.modules, {**stubs, name: self.loaded})
        modules.start()
        self.addCleanup(modules.stop)
        spec.loader.exec_module(self.loaded)
        self.config.Active.Migoto = self.loaded.MigotoManagerConfig()
        self.package = self.loaded.MigotoPackage()
        self.settings = self.config.Active.Migoto
        self.settings.preparation_mode = 'adaptive'
        self.settings.preparation_cpu_cache_enabled = True
        self.settings.preparation_transactional_activation = True
        self.settings.preparation_refresh_key = 'ctrl shift no_alt VK_F8'
        # Explicit mocked local metadata protector, never a production fallback.
        for symbol in ('_seal_snapshot', '_open_snapshot'):
            protector = patch('core.prepared_release.' + symbol, side_effect=lambda data, *args: data)
            protector.start()
            self.addCleanup(protector.stop)
        processes = patch('core.prepared_release._process_names', return_value=['unrelated.exe'])
        self.processes = processes.start()
        self.addCleanup(processes.stop)
        if shared_root is not None:
            # Attach the parent fixture without recreating any package/importer bytes.
            for name, value in shared_state['settings'].items():
                setattr(self.settings, name, value)
            self.config.Active.Importer.deployed_migoto_signatures = shared_state['deployed_signatures']
            self.package._installation_pending = shared_state['installation_pending']
            return
        self.write_package(self.package.package_path, 'old')
        self.write_package(self.source, 'new')
        for name in LIBRARIES[:2]:
            (self.importer / name).write_bytes((self.package.package_path / name).read_bytes())
        (self.importer / 'd3dx.ini').write_bytes(b'[User]\nvalue = original\n[ModLoading]\nmode = adaptive\n')
        (self.importer / 'Mods').mkdir()
        (self.importer / 'Mods' / 'user.ini').write_bytes(b'user mod')
        (self.importer / 'nvapi64.dll').write_bytes(b'unmanaged compatibility library')

    def write_package(self, path, version, *, signed_marker=True):
        for name in LIBRARIES:
            (path / name).write_bytes((version + ' fixture ' + name).encode())
        marker = dict(schema_version=1, mod_loading_version=2, modes=['off', 'adaptive', 'prepared_profile'],
            dll_sha256=hashlib.sha256((path / 'd3d11.dll').read_bytes()).hexdigest(), features=list(V2_FEATURES))
        (path / CAPABILITY_FILE).write_text(json.dumps(marker))
        signatures = {name: fixture_signature((path / name).read_bytes()) for name in (*LIBRARIES, CAPABILITY_FILE)}
        if not signed_marker:
            signatures.pop(CAPABILITY_FILE)
        (path / 'Manifest.json').write_text(json.dumps(dict(version=version, signatures=signatures)))

    def install(self):
        self.package.download_latest_version()
        self.package.install_latest_version(clean=False)

    def managed_bytes(self):
        return {(group, name): (path / name).read_bytes() for group, path, names in (
            ('package', self.package.package_path, (*LIBRARIES, CAPABILITY_FILE, 'Manifest.json')),
            ('importer', self.importer, ('d3d11.dll', 'd3dcompiler_47.dll', 'd3dx.ini')))
            for name in names}

    def test_normal_download_install_and_rollback_preserve_complete_last_good_state(self):
        original = self.managed_bytes()
        original_settings = {key: getattr(self.settings, key) for key in asdict(PreparedLoadingSettings())}
        self.install()
        self.assertTrue(self.package.preparation_capability().supported)
        self.assertEqual(self.settings.preparation_mode, 'adaptive')
        self.assertTrue(self.settings.preparation_cpu_cache_enabled)
        record = self.package.installed_release.inspect(self.package.package_path, self.importer)
        self.assertEqual(record['version'], 'old')
        self.settings.preparation_cpu_cache_mb = 1024
        (self.importer / 'd3dx.ini').write_bytes(b'[User]\nvalue = original\n[ModLoading]\nmode = prepared_profile\n')
        self.package.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), original)
        self.assertEqual({key: getattr(self.settings, key) for key in original_settings}, original_settings)
        self.assertTrue(self.package.update_available())
        self.assertEqual((self.importer / 'Mods' / 'user.ini').read_bytes(), b'user mod')
        self.assertEqual((self.importer / 'nvapi64.dll').read_bytes(), b'unmanaged compatibility library')

    def test_normal_unsigned_capability_clears_activation_even_with_unsafe_preference(self):
        self.write_package(self.source, 'new', signed_marker=False)
        self.settings.unsafe_mode = True
        self.install()
        self.assertFalse(self.package.preparation_capability().supported)
        self.assertEqual(self.settings.preparation_mode, 'off')
        self.assertFalse(self.settings.preparation_cpu_cache_enabled)
        self.assertEqual(self.settings.preparation_refresh_key, '')

    def test_bad_new_dll_never_replaces_either_deployed_library(self):
        before = {name: (self.importer / name).read_bytes() for name in LIBRARIES[:2]}
        (self.source / 'd3d11.dll').write_bytes(b'tampered unsigned fixture')
        self.package.download_latest_version()
        with self.assertRaisesRegex(ValueError, 'signature invalid'):
            self.package.install_latest_version(clean=False)
        self.assertEqual(before, {name: (self.importer / name).read_bytes() for name in before})

    def test_game_alias_blocks_download_install_deployment_and_rollback_before_writes(self):
        self.install()
        before = self.managed_bytes()
        self.processes.return_value = ['FIXTURE-GAME.EXE']
        for action in (self.package.download_latest_version,
                lambda: self.package.install_latest_version(clean=False),
                lambda: self.package.deploy_package_files(force=True), self.package.rollback_installed_package):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'Stop the game'):
                action()
            self.assertEqual(before, self.managed_bytes())

    def test_tampered_last_good_blocks_restore_without_changing_any_managed_file(self):
        self.install()
        before = self.managed_bytes()
        record = self.package.installed_release.inspect(self.package.package_path, self.importer)
        backup = self.package.installed_release.root / record['generation'] / 'package' / '3dmloader.dll'
        backup.write_bytes(b'corrupt backup')
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            self.package.rollback_installed_package()
        self.assertEqual(before, self.managed_bytes())

    def test_download_failure_after_manifest_replacement_restores_prior_installation(self):
        before = self.managed_bytes()
        base = self.loaded.MigotoPackage.__mro__[1]
        download = base.download_latest_version
        def fail_after_manifest(package):
            download(package)
            raise OSError('fixture download failed after manifest replacement')
        with patch.object(base, 'download_latest_version', fail_after_manifest):
            with self.assertRaisesRegex(OSError, 'fixture download failed'):
                self.package.download_latest_version()
        self.assertEqual(self.managed_bytes(), before)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())

    def test_download_failure_when_game_starts_blocks_launch_until_explicit_idle_recovery(self):
        before = self.managed_bytes()
        base = self.loaded.MigotoPackage.__mro__[1]
        download = base.download_latest_version
        def fail_after_manifest(package):
            download(package)
            self.processes.return_value = ['fixture-game.exe']
            raise OSError('fixture interrupted download')
        with patch.object(base, 'download_latest_version', fail_after_manifest), self.assertLogs(self.loaded.log, 'ERROR'):
            with self.assertRaisesRegex(OSError, 'fixture interrupted download'):
                self.package.download_latest_version()
        self.assertTrue((self.importer / '.xxmi-recovery-required.json').exists())
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            self.package.run_pre_launch(types.SimpleNamespace(process_name='fixture-game.exe'))
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            self.package.download_latest_version()
        self.processes.return_value = ['unrelated.exe']
        self.package.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), before)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())

    def test_normal_update_recovers_after_superclass_install_failure_removes_manifest(self):
        before = self.managed_bytes()
        (self.source / 'd3d11.dll').write_bytes(b'unsigned corrupt fixture')
        with self.assertRaisesRegex(ValueError, 'signature invalid'):
            self.package.update()
        self.assertEqual(self.managed_bytes(), before)
        self.assertTrue(self.package.preparation_capability().supported)

    def test_rollback_preserves_later_unrelated_ini_edits(self):
        self.install()
        (self.importer / 'd3dx.ini').write_bytes(
            b'; user comment\n[User]\nvalue = user changed this\n[ModLoading]\nmode = prepared_profile\n[Other]\nkeep = yes\n')
        self.package.rollback_installed_package()
        self.assertEqual((self.importer / 'd3dx.ini').read_bytes(),
            b'; user comment\n[User]\nvalue = user changed this\n[ModLoading]\nmode = adaptive\n[Other]\nkeep = yes\n')

    def test_download_crash_keeps_durable_barrier_before_first_manifest_mutation(self):
        before = self.managed_bytes()
        base = self.loaded.MigotoPackage.__mro__[1]
        download = base.download_latest_version
        def crash_after_manifest(package):
            journal = self.importer / '.xxmi-recovery-required.json'
            self.assertTrue(journal.exists())
            self.assertEqual(_read_json(journal), package.installed_release.pending_record(package.package_path, self.importer))
            download(package)
            raise KeyboardInterrupt('simulated process termination')
        with patch.object(base, 'download_latest_version', crash_after_manifest):
            with self.assertRaises(KeyboardInterrupt):
                self.package.download_latest_version()
        restarted = self.loaded.MigotoPackage()
        self.assertFalse(restarted._installation_pending)
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            restarted.run_pre_launch(types.SimpleNamespace(process_name='fixture-game.exe'))
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            restarted.install_latest_version(clean=False)
        restarted.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), before)

    def test_mid_install_crash_keeps_barrier_and_restart_can_restore_last_good(self):
        before = self.managed_bytes()
        self.package.download_latest_version()
        def partial_move(source, destination):
            (destination / 'd3d11.dll').write_bytes((source / 'd3d11.dll').read_bytes())
            raise KeyboardInterrupt('simulated mid-install termination')
        with patch.object(self.package, 'move_contents', partial_move):
            with self.assertRaises(KeyboardInterrupt):
                self.package.install_latest_version(clean=False)
        restarted = self.loaded.MigotoPackage()
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            restarted.run_pre_launch(types.SimpleNamespace(process_name='fixture-game.exe'))
        restarted.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), before)

    def test_symlink_package_root_is_refused_before_base_download_mutates_target(self):
        package_path = self.package.package_path
        target = package_path.with_name('external-target')
        package_path.rename(target)
        try:
            package_path.symlink_to(target, target_is_directory=True)
        except OSError as error:
            target.rename(package_path)
            self.skipTest(f'Symlink creation unavailable: {error}')
        before = (target / 'Manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Reparse/symlink'):
            self.package.download_latest_version()
        self.assertEqual((target / 'Manifest.json').read_bytes(), before)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())

    def make_retained_recovery(self):
        replace = _StagedFile.replace
        calls = []
        def double_failure(staged, target):
            calls.append(target)
            if len(calls) in (3, 4):
                raise PermissionError('transient commit and revert sharing violation')
            return replace(staged, target)
        with patch.object(_StagedFile, 'replace', double_failure):
            with self.assertRaisesRegex(RuntimeError, 'recovery blocked'):
                self.package.rollback_installed_package()
        journal = self.importer / '.xxmi-recovery-required.json'
        self.assertEqual(_read_json(journal)['kind'], 'managed_file_transaction')
        return journal

    def test_retained_cross_directory_transaction_recovers_after_restart_and_transient_failure(self):
        before = self.managed_bytes()
        self.install()
        self.make_retained_recovery()
        restarted = self.loaded.MigotoPackage()
        restarted.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), before)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())
        self.assertFalse(list(self.importer.glob('.xxmi-*.tmp')))
        self.assertFalse(list(self.package.package_path.glob('.xxmi-*.tmp')))

    def test_retained_recovery_refuses_outside_targets_and_damaged_recovery_bytes(self):
        self.install()
        journal = self.make_retained_recovery()
        value = _read_json(journal)
        original = json.loads(json.dumps(value))
        first = next(iter(value['targets']))
        value['targets'][first] = str(self.root / 'unmanaged.dll')
        _write_json(journal, value)
        before = self.managed_bytes()
        with self.assertRaisesRegex(ValueError, 'Invalid installation recovery transaction'):
            self.package.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), before)
        _write_json(journal, original)
        recovery = Path(next(path for path in original['recovery'].values() if path is not None))
        recovery.write_bytes(b'changed recovery fixture')
        with self.assertRaisesRegex(ValueError, 'Recovery file authentication failed'):
            self.package.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), before)

    def prepare_first_install(self):
        for source in self.package.package_path.iterdir():
            source.unlink()
        self.package.manifest = None
        self.package.download_latest_version()
        self.assertFalse(self.package.installed_release.available(self.importer))
        self.assertIsNone(_read_json(self.importer / '.xxmi-recovery-required.json')['snapshot_sha256'])

    def interrupt_first_install(self):
        self.prepare_first_install()
        replace = _StagedFile.replace
        calls = []
        def double_failure(staged, target):
            calls.append(target)
            if len(calls) in (3, 4):
                raise PermissionError('transient first-install commit and revert failure')
            return replace(staged, target)
        with patch.object(_StagedFile, 'replace', double_failure):
            with self.assertRaisesRegex(RuntimeError, 'recovery blocked'):
                self.package.install_latest_version(clean=False)
        journal = self.importer / '.xxmi-recovery-required.json'
        value = _read_json(journal)
        self.assertIsNone(value['installation']['snapshot_sha256'])
        self.assertEqual(set(value['targets']), {'d3d11.dll', 'd3dcompiler_47.dll'})
        return value

    def test_first_install_interrupted_native_transaction_retries_through_normal_repair(self):
        self.interrupt_first_install()
        restarted = self.loaded.MigotoPackage()
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            restarted.run_pre_launch(types.SimpleNamespace(process_name='fixture-game.exe'))
        base = self.loaded.MigotoPackage.__mro__[1]
        download = base.download_latest_version
        def assert_barrier_during_repair(package):
            value = _read_json(self.importer / '.xxmi-recovery-required.json')
            self.assertEqual(value, package.installed_release.pending_record(package.package_path, self.importer))
            self.assertIsNone(value['snapshot_sha256'])
            download(package)
        with patch.object(base, 'download_latest_version', assert_barrier_during_repair):
            restarted.update()
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())
        self.assertTrue(restarted.preparation_capability().supported)
        self.assertEqual((self.importer / 'Mods' / 'user.ini').read_bytes(), b'user mod')
        self.assertEqual((self.importer / 'nvapi64.dll').read_bytes(), b'unmanaged compatibility library')
        self.assertEqual((self.importer / 'd3dx.ini').read_bytes(), b'[User]\nvalue = original\n[ModLoading]\nmode = adaptive\n')
        self.assertFalse(list(self.importer.glob('.xxmi-*.tmp')))

    def test_first_install_crash_during_cleanup_uses_only_publisher_authenticated_current_native(self):
        value = self.interrupt_first_install()
        Path(value['recovery']['d3d11.dll']).unlink()
        restarted = self.loaded.MigotoPackage()
        restarted.update()
        self.assertTrue(restarted.preparation_capability().supported)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())

    def test_first_install_replay_cleanup_crash_keeps_barrier_for_original_bytes_and_can_retry(self):
        self.interrupt_first_install()
        close = _StagedFile.close
        crashed, staged_files = [], []
        def remember_staging(directory, data):
            staged = _temporary(directory, data)
            staged_files.append(staged)
            return staged
        def crash_after_cleanup(staged, **kwargs):
            path = staged.path
            close(staged, **kwargs)
            if path is not None and not crashed:
                crashed.append(path)
                raise KeyboardInterrupt('simulated replay cleanup crash')
        try:
            with patch.object(_StagedFile, 'close', crash_after_cleanup), \
                    patch('core.prepared_release._temporary', remember_staging):
                with self.assertRaises(KeyboardInterrupt):
                    self.package.installed_release.resume_first_install(self.package.package_path, self.importer,
                        assert_idle=self.package._assert_process_idle)
        finally:
            # A terminated process closes handles without deleting retained files.
            for staged in staged_files:
                close(staged, retain=True)
        journal = self.importer / '.xxmi-recovery-required.json'
        self.assertTrue(_read_json(journal)['preinstall_replay'])
        restarted = self.loaded.MigotoPackage()
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            restarted.run_pre_launch(types.SimpleNamespace(process_name='fixture-game.exe'))
        restarted.update()
        self.assertTrue(restarted.preparation_capability().supported)
        self.assertFalse(journal.exists())

    def test_first_install_missing_recovery_and_unsigned_current_native_refuses_all_writes(self):
        value = self.interrupt_first_install()
        Path(value['recovery']['d3d11.dll']).unlink()
        (self.importer / 'd3d11.dll').write_bytes(b'unsigned current bytes')
        before = self.managed_bytes()
        restarted = self.loaded.MigotoPackage()
        with self.assertRaisesRegex(ValueError, 'current native file is not authenticated'):
            restarted.download_latest_version()
        self.assertEqual(self.managed_bytes(), before)
        self.assertTrue((self.importer / '.xxmi-recovery-required.json').exists())

    def test_first_install_damaged_retained_bytes_refuses_repair_before_any_target_write(self):
        value = self.interrupt_first_install()
        Path(value['recovery']['d3dcompiler_47.dll']).write_bytes(b'damaged recovery original')
        before = self.managed_bytes()
        restarted = self.loaded.MigotoPackage()
        with self.assertRaisesRegex(ValueError, 'Recovery file authentication failed'):
            restarted.download_latest_version()
        self.assertEqual(self.managed_bytes(), before)
        self.assertTrue((self.importer / '.xxmi-recovery-required.json').exists())

    def test_first_install_crash_after_native_commit_restarts_normal_installation(self):
        self.prepare_first_install()
        with patch.object(self.loaded, 'reconcile_preparation_settings', side_effect=KeyboardInterrupt('simulated finish crash')):
            with self.assertRaises(KeyboardInterrupt):
                self.package.install_latest_version(clean=False)
        restarted = self.loaded.MigotoPackage()
        restarted.update()
        self.assertTrue(restarted.preparation_capability().supported)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())

    def crash_after_one_native_commit(self, *, force=False):
        script = Path(__file__).resolve()
        repository = script.parents[1]
        state_path = self.root / 'native-commit-child.json'
        state_path.write_text(json.dumps(dict(settings=asdict(self.settings),
            deployed_signatures=self.config.Active.Importer.deployed_migoto_signatures,
            installation_pending=self.package._installation_pending)))
        environment = os.environ.copy()
        environment.update(PYTHONPATH=str(repository / 'src' / 'xxmi_launcher'),
            PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(self.root.parent),
            TEMP=str(self.root.parent), TMP=str(self.root.parent))
        result = subprocess.run([sys.executable, '-B', str(script), '--native-commit-child',
            str(self.root), str(state_path), 'force' if force else 'normal'],
            cwd=repository, env=environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        value = _read_json(self.importer / '.xxmi-recovery-required.json')
        self.assertEqual(value['targets'], {'d3d11.dll': str(self.importer / 'd3d11.dll')})
        return value

    def test_one_missing_dll_production_deployment_crash_rolls_back_after_restart(self):
        original = self.managed_bytes()
        self.install()
        (self.importer / 'd3d11.dll').unlink()
        self.crash_after_one_native_commit()
        restarted = self.loaded.MigotoPackage()
        restarted.rollback_installed_package()
        self.assertEqual(self.managed_bytes(), original)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())
        self.assertEqual((self.importer / 'Mods' / 'user.ini').read_bytes(), b'user mod')

    def test_first_install_one_dll_production_crash_retries_normal_repair(self):
        self.prepare_first_install()
        self.package.move_contents(self.source, self.package.package_path)
        self.package.manifest = None
        self.settings.unsafe_mode = True  # Existing user preference skips their third-party compiler.
        (self.importer / 'd3d11.dll').unlink()
        (self.importer / 'd3dcompiler_47.dll').write_bytes(b'preexisting user compiler')
        self.crash_after_one_native_commit(force=True)
        restarted = self.loaded.MigotoPackage()
        restarted.update()
        self.assertTrue(restarted.preparation_capability().supported)
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())
        self.assertEqual((self.importer / 'd3dcompiler_47.dll').read_bytes(), b'preexisting user compiler')
        self.assertEqual((self.importer / 'Mods' / 'user.ini').read_bytes(), b'user mod')

    def test_native_subset_requires_exact_foreign_key_and_target_refusal(self):
        self.install()
        journal = self.make_retained_recovery()
        original = _read_json(journal)
        for key, target in (('foreign.dll', str(self.importer / 'd3d11.dll')),
                ('d3d11.dll', str(self.root / 'outside.dll')), ('d3d11.dll', str(self.importer / 'd3dcompiler_47.dll'))):
            value = json.loads(json.dumps(original))
            for field in ('targets', 'recovery', 'recovery_sha256', 'prepared_sha256'):
                source = original[field]['d3d11.dll']
                value[field] = {key: target if field == 'targets' else source}
            _write_json(journal, value)
            before = self.managed_bytes()
            with self.subTest(key=key, target=target), self.assertRaisesRegex(ValueError, 'Invalid installation recovery transaction'):
                self.package.rollback_installed_package()
            self.assertEqual(self.managed_bytes(), before)

    def test_cross_directory_rollback_failure_restores_both_groups(self):
        self.install()
        before = self.managed_bytes()
        replace = _StagedFile.replace
        calls = []
        def fail_once(staged, target):
            calls.append(target)
            if len(calls) == 9:  # Journal + six package files + first importer file + failure.
                raise PermissionError('injected cross-directory sharing violation')
            return replace(staged, target)
        with patch.object(_StagedFile, 'replace', fail_once):
            with self.assertRaisesRegex(PermissionError, 'injected cross-directory'):
                self.package.rollback_installed_package()
        self.assertEqual(before, self.managed_bytes())
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())


def native_commit_child(shared_root, state_path, force):
    state_path = Path(state_path).resolve(strict=True)
    if state_path.parent != Path(shared_root).resolve(strict=True):
        raise ValueError('Crash child state must be inside the attached fixture')
    fixture = InstalledPackageTests()
    fixture.setUp(shared_root=shared_root, shared_state=json.loads(state_path.read_text()))
    replace = _StagedFile.replace
    def terminate_after_commit(staged, target):
        replace(staged, target)
        if target == fixture.importer / 'd3d11.dll':
            os._exit(0)  # Deliberately bypass finally: OS closes native staging handles.
    with patch.object(_StagedFile, 'replace', terminate_after_commit):
        fixture.package.deploy_package_files(force=force)
    os._exit(3)  # The intended production commit must be reached.


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--native-commit-child':
        native_commit_child(sys.argv[2], sys.argv[3], sys.argv[4] == 'force')
    else:
        unittest.main()
