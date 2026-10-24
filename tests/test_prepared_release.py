import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.prepared_release import DevelopmentRelease, LIBRARIES, _StagedFile, assert_process_idle
from core.prepared_loading import CAPABILITY_FILE
from core.utils.security import Security


class DevelopmentReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.security = Security()
        self.security.generate_key_pair()  # Temporary fixture key only.
        # Production uses Windows DPAPI. This authenticated fixture protector is
        # isolated to temporary tests and never changes publisher trust.
        import hmac
        from unittest.mock import patch
        secret = b'temporary snapshot fixture secret'
        def seal(data):
            return hmac.digest(secret, data, 'sha256') + data
        def unseal(data):
            if not hmac.compare_digest(data[:32], hmac.digest(secret, data[32:], 'sha256')):
                raise ValueError('fixture seal invalid')
            return data[32:]
        for name, operation in (('_seal_snapshot', seal), ('_open_snapshot', unseal)):
            mocked = patch('core.prepared_release.' + name, operation)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.release = DevelopmentRelease(self.root / 'isolated', self.security)
        self.importer = self.root / 'importer'
        self.importer.mkdir()
        self.prior = self.root / 'Manifest.json'
        self.config = b'[Rendering]\nenabled = 1\n'
        (self.importer / 'd3dx.ini').write_bytes(self.config)
        (self.importer / 'Mods').mkdir()
        self.mod = self.importer / 'Mods' / 'user.ini'
        self.mod.write_bytes(b'untouched mod')
        signatures = {}
        for name in LIBRARIES[:2]:
            data = b'prior signed fixture ' + name.encode()
            (self.importer / name).write_bytes(data)
            signatures[name] = self.security.sign(data)
        self.prior.write_text(json.dumps(dict(version='1.0', signatures=signatures)))
        self.old_dll = (self.importer / 'd3d11.dll').read_bytes()

    def archive(self, **overrides):
        files = {name: b'new signed fixture ' + name.encode() for name in LIBRARIES}
        marker = dict(schema_version=1, mod_loading_version=1, modes=['off', 'adaptive'],
                      dll_sha256=hashlib.sha256(files['d3d11.dll']).hexdigest(),
                      release_stage='development', package_version='2.0-test')
        marker.update(overrides)
        files[CAPABILITY_FILE] = json.dumps(marker).encode()
        files['Manifest.json'] = json.dumps(dict(version='2.0-test', signatures={
            name: self.security.sign(data) for name, data in files.items()})).encode()
        path = self.root / 'release.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return path, self.security.sign(path.read_bytes())

    def test_signed_selection_is_isolated_persistent_and_rechecks_tampering(self):
        archive, signature = self.archive()
        self.release.snapshot(self.importer, self.prior)
        self.release.select(archive, signature, version='2.0-test', importer=self.importer)
        restarted = DevelopmentRelease(self.release.root, self.security)
        selected = restarted.resolve()
        self.assertNotEqual(selected, self.importer)
        self.assertEqual((self.importer / 'd3d11.dll').read_bytes(), self.old_dll)
        (selected / 'd3d11.dll').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            restarted.resolve()

    def test_unsigned_and_wrong_stage_version_fail_without_selection(self):
        archive, signature = self.archive()
        for sig, version in [('', '2.0-test'), (signature, '2.1')]:
            with self.assertRaises(ValueError):
                self.release.select(archive, sig, version=version, importer=self.importer)
        archive, signature = self.archive(release_stage='production')
        with self.assertRaises(ValueError):
            self.release.select(archive, signature, version='2.0-test', importer=self.importer)
        self.assertFalse(self.release.selected)

    def test_existing_pin_cannot_mix_individually_signed_files_from_another_archive(self):
        archive, signature = self.archive()
        pinned = self.release.root / hashlib.sha256(archive.read_bytes()).hexdigest()
        pinned.mkdir(parents=True)
        with zipfile.ZipFile(archive) as package:
            package.extractall(pinned)
        alternate = b'another publisher-signed compiler fixture'
        (pinned / 'd3dcompiler_47.dll').write_bytes(alternate)
        manifest = json.loads((pinned / 'Manifest.json').read_bytes())
        manifest['signatures']['d3dcompiler_47.dll'] = self.security.sign(alternate)
        (pinned / 'Manifest.json').write_text(json.dumps(manifest))
        # Every member passes individual publisher authentication, but this
        # compiler/manifest pair is absent from the selected signed archive.
        self.release.verify(pinned, version='2.0-test', stage='development')
        with self.assertRaisesRegex(ValueError, 'differs from authenticated archive'):
            self.release.select(archive, signature, version='2.0-test', importer=self.importer)
        self.assertFalse(self.release.selected)
        self.assertEqual((self.importer / 'd3d11.dll').read_bytes(), self.old_dll)

    def test_existing_pin_rejects_extra_files_and_selection_seals_archive_member_hashes(self):
        archive, signature = self.archive()
        pinned = self.release.root / hashlib.sha256(archive.read_bytes()).hexdigest()
        pinned.mkdir(parents=True)
        with zipfile.ZipFile(archive) as package:
            package.extractall(pinned)
            expected = {name: hashlib.sha256(package.read(name)).hexdigest() for name in package.namelist()}
        extra = pinned / 'unexpected.dll'
        extra.write_bytes(b'extra fixture')
        with self.assertRaisesRegex(ValueError, 'differs from authenticated archive'):
            self.release.select(archive, signature, version='2.0-test', importer=self.importer)
        self.assertFalse(self.release.selected)
        extra.unlink()
        self.release.select(archive, signature, version='2.0-test', importer=self.importer)
        self.assertEqual(self.release.selection()['hashes'], expected)
        extra.write_bytes(b'extra fixture after selection')
        with self.assertRaisesRegex(ValueError, 'Pinned development package changed'):
            self.release.resolve(self.importer)

    def test_publisher_signed_archive_cannot_smuggle_mods_or_paths(self):
        archive, _ = self.archive()
        with zipfile.ZipFile(archive, 'a') as package:
            package.writestr('../outside.dll', b'no')
        with self.assertRaises(ValueError):
            self.release.select(archive, self.security.sign(archive.read_bytes()), version='2.0-test', importer=self.importer)
        self.assertFalse((self.root / 'outside.dll').exists())

    def test_rollback_restores_prior_dll_and_exact_configuration_leaves_mods(self):
        self.release.snapshot(self.importer, self.prior)
        (self.importer / 'd3d11.dll').write_bytes(b'new build')
        (self.importer / 'd3dx.ini').write_bytes(b'new managed config')
        checks = []
        self.release.rollback(self.importer, assert_idle=lambda: checks.append(True))
        self.assertEqual(len(checks), 2)
        self.assertEqual((self.importer / 'd3d11.dll').read_bytes(), self.old_dll)
        self.assertEqual((self.importer / 'd3dx.ini').read_bytes(), self.config)
        self.assertEqual(self.mod.read_bytes(), b'untouched mod')

    def test_rollback_refuses_active_process_or_damaged_backup_before_any_write(self):
        backup = self.release.snapshot(self.importer, self.prior)
        (self.importer / 'd3dx.ini').write_bytes(b'current config')
        def active():
            raise ValueError('process active')
        with self.assertRaisesRegex(ValueError, 'active'):
            self.release.rollback(self.importer, assert_idle=active)
        (backup / 'd3d11.dll').write_bytes(b'corrupted prior DLL')
        with self.assertRaisesRegex(ValueError, 'verification'):
            self.release.rollback(self.importer, assert_idle=lambda: None)
        self.assertEqual((self.importer / 'd3dx.ini').read_bytes(), b'current config')

    def test_snapshot_rejects_unsigned_prior_library(self):
        (self.importer / 'd3d11.dll').write_bytes(b'unknown DLL')
        with self.assertRaisesRegex(ValueError, 'unauthenticated'):
            self.release.snapshot(self.importer, self.prior)

    def test_migoto_pin_blocks_updater_repair_and_uninstall_then_rolls_back(self):
        """Exercise real Migoto lifecycle with platform services stubbed, no DLLs executed."""
        import importlib.util
        import sys
        import types
        from unittest.mock import MagicMock, patch
        from core.prepared_loading import PreparedLoadingSettings
        security = self.security
        root = self.root
        class FakePackage:
            update = MagicMock()
            def __init__(self, metadata):
                self.metadata = metadata
                self.security = security
                self.package_path = root / 'Resources' / 'Packages' / 'XXMI'
                self.manifest = None
            def load_manifest(self):
                self.manifest = json.loads((self.package_path / 'Manifest.json').read_text())
            def get_signature(self, path):
                return self.manifest['signatures'][path.name]
        def module(name, **values):
            value = types.ModuleType(name)
            value.__dict__.update(values)
            return value
        config = module('core.config_manager', Active=types.SimpleNamespace(
            Importer=types.SimpleNamespace(importer_path=self.importer, deployed_migoto_signatures={},
                process_exe_names=['fixture-game.exe', 'fixture-game-alias.exe'], game_exe_names=['fixture-game.exe']),
            Migoto=PreparedLoadingSettings()))
        stubs = {
            'core.error_manager': module('core.error_manager'),
            'core.path_manager': module('core.path_manager'),
            'core.event_manager': MagicMock(),
            'core.config_manager': config,
            'core.locale_manager': module('core.locale_manager', L=lambda key, text: text),
            'core.package_manager': module('core.package_manager', Package=FakePackage,
                PackageMetadata=lambda **kw: types.SimpleNamespace(**kw)),
            'core.config.enums': module('core.config.enums',
                InputDisableMode=types.SimpleNamespace(MODS=0), LogLevel=types.SimpleNamespace(DISABLED=0)),
            'core.utils.dll_injector': module('core.utils.dll_injector', DllInjector=MagicMock()),
            'core.utils.process_manager': module('core.utils.process_manager', LaunchContext=object),
        }
        name = '_migoto_lifecycle_fixture'
        source = Path(__file__).parents[1] / 'src/xxmi_launcher/core/packages/migoto_package.py'
        spec = importlib.util.spec_from_file_location(name, source)
        loaded = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {**stubs, name: loaded}), \
                patch('core.prepared_release._process_names', return_value=['unrelated.exe']) as processes:
            spec.loader.exec_module(loaded)
            package = loaded.MigotoPackage()
            package.package_path.mkdir(parents=True)
            (package.package_path / 'Manifest.json').write_bytes(self.prior.read_bytes())
            archive, signature = self.archive()
            package.select_development_package(archive, signature, version='2.0-test')
            self.assertFalse(package.update_available())
            for action in (lambda: package.update(), package.download_latest_version,
                           lambda: package.install_latest_version(False), package.uninstall,
                           lambda: package.deploy_package_files(force=True)):
                with self.assertRaises(ValueError):
                    action()
            FakePackage.update.assert_not_called()
            # Simulate a deployed development library/config, then exercise the public rollback API.
            (self.importer / 'd3d11.dll').write_bytes(b'development deployment')
            (self.importer / 'd3dx.ini').write_bytes(b'development managed configuration')
            # Selected deployment always replaces untrusted active bytes even
            # when the legacy unsafe-mode preference is enabled, and retains nvapi.
            package_config = config.Active.Migoto
            package_config.unsafe_mode = True
            nvapi = self.importer / 'nvapi64.dll'
            nvapi.write_bytes(b'prior nvapi retained')
            package.validate_deployed_files = MagicMock()
            context = types.SimpleNamespace(process_name='fixture-game.exe')
            package.run_pre_launch(context)
            self.assertEqual((self.importer / 'd3d11.dll').read_bytes(), b'new signed fixture d3d11.dll')
            self.assertEqual(nvapi.read_bytes(), b'prior nvapi retained')
            package.validate_deployed_files.assert_called_once()
            processes.return_value = ['fixture-game.exe']
            with self.assertRaisesRegex(ValueError, 'Stop the game'):
                package.rollback_development_package(context)
            self.assertTrue(package.development_release.selected)
            processes.return_value = ['fixture-game-alias.exe']
            with self.assertRaisesRegex(ValueError, 'Stop the game'):
                package.rollback_development_package(context)
            self.assertTrue(package.development_release.selected)
            processes.return_value = ['unrelated.exe']
            package.rollback_development_package(context)
            self.assertFalse(package.development_release.selected)
            self.assertEqual(package.package_path, package.upstream_package_path)
            self.assertEqual((self.importer / 'd3d11.dll').read_bytes(), self.old_dll)
            self.assertEqual((self.importer / 'd3dx.ini').read_bytes(), self.config)
            self.assertEqual(self.mod.read_bytes(), b'untouched mod')

    def test_failed_second_replacement_recovers_complete_pre_attempt_state(self):
        from unittest.mock import patch
        self.release.snapshot(self.importer, self.prior)
        for name in ('d3d11.dll', 'd3dcompiler_47.dll', 'd3dx.ini'):
            (self.importer / name).write_bytes(b'current development ' + name.encode())
        before = {name: (self.importer / name).read_bytes() for name in ('d3d11.dll', 'd3dcompiler_47.dll', 'd3dx.ini')}
        replace = _StagedFile.replace
        failed = []
        def sharing_violation(source, target):
            if Path(target).name == 'd3dcompiler_47.dll' and not failed:
                failed.append(True)
                raise PermissionError('injected second-file sharing violation')
            return replace(source, target)
        with patch.object(_StagedFile, 'replace', sharing_violation):
            with self.assertRaisesRegex(PermissionError, 'sharing violation'):
                self.release.rollback(self.importer, assert_idle=lambda: None)
        self.assertEqual(before, {name: (self.importer / name).read_bytes() for name in before})
        self.assertFalse(list(self.importer.glob('.xxmi-*.tmp')))
        self.assertFalse((self.importer / '.xxmi-recovery-required.json').exists())
        self.assertEqual(self.mod.read_bytes(), b'untouched mod')

    def test_blocked_recovery_retains_authenticated_journal_and_original_bytes(self):
        from unittest.mock import patch
        from core.prepared_release import _read_json, _replace_set
        contents = {name: b'candidate ' + name.encode() for name in LIBRARIES[:2]}
        before = {name: (self.importer / name).read_bytes() for name in contents}
        replace = _StagedFile.replace
        changes = []
        def blocked(source, target):
            changes.append(Path(target).name)
            if Path(target).name in contents and len(changes) > 2:
                raise PermissionError('injected commit/recovery sharing violation')
            return replace(source, target)
        # The first replacement writes the recovery journal, then d3d11 commits.
        with patch.object(_StagedFile, 'replace', blocked):
            with self.assertRaisesRegex(RuntimeError, 'recovery blocked'):
                _replace_set(self.importer, contents, lambda: None)
        journal = self.importer / '.xxmi-recovery-required.json'
        recovery = _read_json(journal)['recovery']
        self.assertEqual(before, {name: Path(path).read_bytes() for name, path in recovery.items()})
        self.assertEqual(set(self.importer.glob('.xxmi-*.tmp')), {Path(path) for path in recovery.values()})
        with self.assertRaisesRegex(ValueError, 'requires recovery'):
            _replace_set(self.importer, contents, lambda: None)

    def test_fixed_temp_symlink_is_never_followed_and_managed_symlink_is_rejected(self):
        self.release.snapshot(self.importer, self.prior)
        fixed = self.importer / 'd3d11.dll.rollback'
        fixed.symlink_to(self.mod)
        self.release.rollback(self.importer, assert_idle=lambda: None)
        self.assertTrue(fixed.is_symlink())
        self.assertEqual(self.mod.read_bytes(), b'untouched mod')

        (self.importer / 'd3d11.dll').unlink()
        (self.importer / 'd3d11.dll').symlink_to(self.mod)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.release.rollback(self.importer, assert_idle=lambda: None)
        self.assertEqual(self.mod.read_bytes(), b'untouched mod')

    def test_unsigned_absence_record_cannot_authorize_deletion(self):
        backup = self.release.snapshot(self.importer, self.prior)
        (backup / 'snapshot.json').write_text(json.dumps({name: None for name in ('d3d11.dll', 'd3dcompiler_47.dll', 'd3dx.ini')}))
        with self.assertRaisesRegex(ValueError, 'authentication'):
            self.release.rollback(self.importer, assert_idle=lambda: None)
        self.assertEqual((self.importer / 'd3d11.dll').read_bytes(), self.old_dll)
        self.assertEqual((self.importer / 'd3dx.ini').read_bytes(), self.config)

    def test_deployment_rechecks_immutable_bytes_after_resolve_and_never_follows_symlinks(self):
        from unittest.mock import patch
        archive, signature = self.archive()
        self.release.select(archive, signature, version='2.0-test', importer=self.importer)
        pinned = self.release.resolve(self.importer)
        original = self.release.resolve
        def swap_after_resolve(importer):
            path = original(importer)
            (path / 'd3d11.dll').write_bytes(b'unsigned source swap')
            return path
        with patch.object(self.release, 'resolve', swap_after_resolve):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.release.deploy(self.importer, assert_idle=lambda: None)
        self.assertEqual((self.importer / 'd3d11.dll').read_bytes(), self.old_dll)
        (pinned / 'd3d11.dll').write_bytes(b'new signed fixture d3d11.dll')
        (self.importer / 'd3d11.dll').unlink()
        (self.importer / 'd3d11.dll').symlink_to(self.mod)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.release.deploy(self.importer, assert_idle=lambda: None)
        self.assertEqual(self.mod.read_bytes(), b'untouched mod')


class ProcessIdleTests(unittest.TestCase):
    def test_access_denied_and_incomplete_snapshots_fail_closed(self):
        from unittest.mock import patch
        for error in (PermissionError('AccessDenied'), OSError('snapshot failed')):
            with self.subTest(error=error), patch('core.prepared_release._process_names', side_effect=error):
                with self.assertRaisesRegex(ValueError, 'Cannot verify process identity'):
                    assert_process_idle({'game.exe'})
        for processes in ([], [''], ['unrelated.exe', None]):
            with self.subTest(processes=processes), \
                    patch('core.prepared_release._process_names', return_value=processes):
                with self.assertRaisesRegex(ValueError, 'Cannot verify process identity'):
                    assert_process_idle({'game.exe'})

    def test_complete_snapshot_checks_names_case_insensitively(self):
        from unittest.mock import patch
        with patch('core.prepared_release._process_names', return_value=['unrelated.exe', 'GAME.EXE']):
            with self.assertRaisesRegex(ValueError, 'Stop the game'):
                assert_process_idle({'game.exe'})
        with patch('core.prepared_release._process_names', return_value=['unrelated.exe']):
            assert_process_idle({'game.exe'})

    def test_missing_or_invalid_configured_names_fail_closed(self):
        for names in (set(), {''}, {None}, {'folder/game.exe'}):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, 'valid executable names'):
                assert_process_idle(names)


@unittest.skipUnless(os.name == 'nt', 'requires actual Windows DPAPI and protected file handles')
class WindowsLifecycleTests(unittest.TestCase):
    def test_real_dpapi_metadata_round_trip_and_tampering(self):
        import base64
        from core.prepared_release import _read_json, _write_json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metadata.json'
            value = {'d3d11.dll': {'sha256': 'fixture'}, 'd3dx.ini': None}
            _write_json(path, value)
            self.assertEqual(_read_json(path), value)
            wrapped = json.loads(path.read_bytes())
            sealed = bytearray(base64.b64decode(wrapped['sealed']))
            sealed[-1] ^= 1
            path.write_text(json.dumps({'sealed': base64.b64encode(sealed).decode()}))
            with self.assertRaisesRegex(ValueError, 'authentication failed'):
                _read_json(path)

    def test_real_protected_staging_blocks_write_delete_and_path_substitution(self):
        from core.prepared_release import _replace_set
        with tempfile.TemporaryDirectory() as directory:
            importer = Path(directory)
            contents = {name: b'verified fixture ' + name.encode() for name in LIBRARIES[:2]}
            for name in contents:
                (importer / name).write_bytes(b'prior fixture')
            attempted = []
            def attack_before_commit():
                for temporary in importer.glob('.xxmi-*.tmp'):
                    attempted.append(temporary)
                    with self.assertRaises(PermissionError):
                        temporary.write_bytes(b'unsigned overwrite')
                    with self.assertRaises(PermissionError):
                        temporary.unlink()
                    substitute = importer / 'attacker.tmp'
                    substitute.write_bytes(b'unsigned replacement')
                    with self.assertRaises(PermissionError):
                        os.replace(substitute, temporary)
                    substitute.unlink()
            _replace_set(importer, contents, attack_before_commit)
            self.assertEqual(len(attempted), 4)  # Both payloads and both recovery files.
            self.assertEqual(contents, {name: (importer / name).read_bytes() for name in contents})
            self.assertFalse(list(importer.glob('.xxmi-*.tmp')))
            self.assertFalse((importer / '.xxmi-recovery-required.json').exists())

    def test_real_toolhelp_snapshot_finds_current_process_without_process_access(self):
        from core.prepared_release import _process_names
        names = _process_names()
        self.assertIn(Path(sys.executable).name.casefold(), {name.casefold() for name in names})
        with self.assertRaisesRegex(ValueError, 'Stop the game'):
            assert_process_idle({Path(sys.executable).name})
        assert_process_idle({'xxmi-absent-' + os.urandom(16).hex() + '.exe'})

    def test_toolhelp_unknown_name_and_partial_enumeration_fail_closed(self):
        import ctypes
        from unittest.mock import MagicMock, patch
        for failure in ('create', 'first', 'next', 'unknown'):
            with self.subTest(failure=failure):
                kernel = MagicMock()
                kernel.CreateToolhelp32Snapshot.return_value = (
                    ctypes.c_void_p(-1).value if failure == 'create' else 123)
                def first(snapshot, pointer):
                    if failure == 'first':
                        ctypes.set_last_error(5)
                        return False
                    pointer._obj.szExeFile = '' if failure == 'unknown' else 'unrelated.exe'
                    return True
                kernel.Process32FirstW.side_effect = first
                def next_process(snapshot, pointer):
                    ctypes.set_last_error(5)
                    return False
                kernel.Process32NextW.side_effect = next_process
                with patch('core.prepared_release._windows_kernel', return_value=kernel):
                    with self.assertRaisesRegex(ValueError, 'Cannot verify process identity'):
                        assert_process_idle({'game.exe'})
                if failure != 'create':
                    kernel.CloseHandle.assert_called_once_with(123)
