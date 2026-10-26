'''Portable static checks and opt-in, isolated, inert Windows MSI lifecycle tests.'''
import ctypes
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import unittest
import uuid
import zlib


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('original_msi_validator', ROOT / 'tools/Installer/validate_msi.py')
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
authoring = validator.authoring
INERT = b'INERT XXMI INSTALLER FIXTURE; NEVER EXECUTE\n'


def fixture_payload(root, version):
    files = {
        authoring.EXECUTABLE: INERT + version.encode('ascii'),
        'Resources/Bin/_internal/runtime.dll': INERT + b'fixture runtime ' + version.encode('ascii'),
        'Themes/Default/theme.json': INERT + b'fixture theme',
        'Locale/en/strings.json': INERT + b'fixture locale ' + version.encode('ascii'),
        'LICENSE': INERT + b'Fixture license',
        'Themes/obsolete.txt' if version == '1.0.0' else 'Themes/new.txt': INERT + b'fixture versioned asset',
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {'format': 1, 'version': version, 'launcher_source': 'b' * 40,
                'source_input_status': '', 'source_has_local_changes': False,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (root / authoring.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    return files


def cabinet(files, compressed=True):
    '''Small, independent CAB fixtures; no external compressor or extraction.'''
    file_table, contents = bytearray(), bytearray()
    for name, data in files.items():
        file_table.extend(struct.pack('<IIHHHH', len(data), len(contents), 0, 0, 0, 0))
        file_table.extend(name.encode('ascii') + b'\0')
        contents.extend(data)
    blocks, history = bytearray(), b''
    for offset in range(0, len(contents), 32768):
        raw = bytes(contents[offset:offset + 32768])
        if compressed:
            encoder = zlib.compressobj(wbits=-15, zdict=history) if history else zlib.compressobj(wbits=-15)
            encoded = b'CK' + encoder.compress(raw) + encoder.flush()
        else:
            encoded = raw
        blocks.extend(struct.pack('<IHH', 0, len(encoded), len(raw)) + encoded)
        history = (history + raw)[-32768:]
    data_offset = 44 + len(file_table)
    total = data_offset + len(blocks)
    header = struct.pack('<4sIIIIIBBHHHHH', b'MSCF', 0, total, 0, 44, 0, 3, 1, 1, len(files), 0, 0, 0)
    return header + struct.pack('<IHH', data_offset, (len(contents) + 32767) // 32768, int(compressed)) + file_table + blocks


class InstallerStaticValidationTests(unittest.TestCase):
    def test_mszip_dictionary_and_uncompressed_hashes(self):
        files = {'File_one': b'abcd' * 20000, 'File_two': b'xyz' * 19000}
        expected = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                self.assertEqual(validator.cabinet_hashes(cabinet(files, compressed), {name: len(data) for name, data in files.items()}), expected)

    def test_cabinet_rejects_bad_headers_names_offsets_and_output_bounds(self):
        original = cabinet({'File_one': b'a' * 100})
        for label, mutate in (
                ('length', lambda data: struct.pack_into('<I', data, 8, len(data) + 1)),
                ('spanning', lambda data: struct.pack_into('<H', data, 30, 1)),
                ('compression', lambda data: struct.pack_into('<H', data, 42, 3)),
                ('file offset', lambda data: struct.pack_into('<I', data, 48, 1)),
                ('file size', lambda data: struct.pack_into('<I', data, 44, 101)),
                ('unsafe name', lambda data: data.__setitem__(slice(60, 68), b'../x.exe')),
                ('output bound', lambda data: struct.pack_into('<H', data, struct.unpack_from('<I', data, 36)[0] + 6, 1))):
            damaged = bytearray(original)
            mutate(damaged)
            with self.subTest(label=label), self.assertRaises(ValueError):
                validator.cabinet_hashes(bytes(damaged), {'File_one': 100})
        with self.assertRaises(ValueError):
            validator.cabinet_hashes(original[:-1], {'File_one': 100})

    def test_unknown_side_effect_tables_rejected_before_other_checks(self):
        for name in ('ServiceInstall', 'Environment', 'RemoveRegistry', 'MoveFile', 'SelfReg', 'MsiServiceConfig'):
            with self.subTest(table=name), self.assertRaisesRegex(ValueError, 'side-effect table'):
                validator.validate_tables({name: [{'anything': 'present'}]}, 'x64', {}, authoring.PRODUCTION_IDENTITY)

    def test_official_wix3141_ui_binary_ids_and_unknown_id_diagnostic(self):
        validator.validate_ui_binaries([{'Name': name} for name in (
            'WixUIWixca', 'WixUI_Bmp_Banner', 'WixUI_Bmp_Dialog', 'WixUI_Ico_Info',
            'WixUI_Ico_Exclam', 'WixUI_Bmp_New', 'WixUI_Bmp_Up')])
        for name in ('WixUI_Ico_New', 'WixUI_Ico_Up', 'LaunchApplication'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name):
                validator.validate_ui_binaries([{'Name': name}])

    @unittest.skipIf(os.name == 'nt', 'Non-Windows fail-closed check')
    def test_compiled_database_validation_requires_windows(self):
        with self.assertRaisesRegex(RuntimeError, 'requires Windows'):
            validator.ReadOnlyDatabase('never-opened.msi')


def require_high_integrity():
    '''Inspect the existing token. This function cannot request elevation.'''
    from ctypes import wintypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise AssertionError('Installer fixtures require an already elevated administrator; no UAC/elevation will be requested')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    security.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    security.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    security.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        count = security.GetSidSubAuthorityCount(sid)[0]
        if not count or security.GetSidSubAuthority(sid, count - 1)[0] < 0x3000:
            raise AssertionError('Installer fixtures require an existing high-integrity token; no UAC/elevation will be requested')
    finally:
        kernel.CloseHandle(token)


@unittest.skipUnless(os.name == 'nt' and os.environ.get('XXMI_RUN_INSTALLER_FIXTURES') == '1',
                     'Windows inert MSI fixtures are explicitly opt-in')
class WindowsInstallerFixtureTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.environ.get('GITHUB_ACTIONS'), 'true', 'Installer fixtures are restricted to the opted-in ephemeral GitHub Windows job')
        self.assertEqual(os.environ.get('RUNNER_OS'), 'Windows')
        self.assertEqual(Path(os.environ['GITHUB_WORKSPACE']).resolve(), ROOT)
        require_high_integrity()
        self.identifier = uuid.uuid4().hex
        fixture_parent = ROOT / 'work/installer-fixtures'
        fixture_parent.mkdir(parents=True, exist_ok=True)
        self.directory = fixture_parent / self.identifier
        self.directory.mkdir()
        self.appdir = self.directory / 'application root with spaces'
        self.identity = replace(authoring.PRODUCTION_IDENTITY,
                                product_name='XXMI Installer Fixture ' + self.identifier,
                                upgrade_code='{' + str(uuid.uuid4()).upper() + '}',
                                registry_key='SOFTWARE\\XXMIInstallerTests\\' + self.identifier,
                                default_directory_name='XXMI Fixture ' + self.identifier,
                                shortcut_name='XXMI Fixture ' + self.identifier,
                                artifact_prefix='XXMI-Fixture-' + self.identifier)
        self.fixtures = {}
        self.cleanup_codes = set()
        self.addCleanup(self.cleanup_products)
        self.msiexec = Path(os.environ['SystemRoot']) / 'System32/msiexec.exe'
        self.assertTrue(self.msiexec.is_file())
        candidates = [Path(os.environ['WIX']) / 'bin', Path(os.environ['WIX'])] if os.environ.get('WIX') else []
        candidates.append(Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'WiX Toolset v3.14/bin')
        found = shutil.which('candle.exe')
        if found:
            candidates.append(Path(found).parent)
        wix = next((path for path in candidates if (path / 'candle.exe').is_file() and (path / 'light.exe').is_file()), None)
        self.assertIsNotNone(wix, 'Preinstalled WiX 3 is required; the fixture test downloads no tools')
        self.candle, self.light = wix / 'candle.exe', wix / 'light.exe'

    def run_tool(self, arguments, log, accepted=(0,)):
        with (self.directory / log).open('wb') as stream:
            result = subprocess.run([str(value) for value in arguments], cwd=self.directory, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=180, check=False)
        self.assertIn(result.returncode, accepted, f'Installer fixture command failed ({result.returncode}); see {self.directory / log}')

    def build(self, version):
        directory = self.directory / version
        payload = directory / 'payload'
        expected = fixture_payload(payload, version)
        result = authoring.generate_source(payload, version, directory / 'build', self.identity)
        object_path = result['source'].with_suffix('.wixobj')
        self.run_tool([self.candle, '-nologo', '-arch', 'x64', '-ext', 'WixUIExtension', '-out', object_path, result['source']], version + '-candle.log')
        self.run_tool([self.light, '-nologo', '-ext', 'WixUIExtension', '-out', result['msi'], object_path], version + '-light.log')
        fixture = {'payload': payload, 'version': version, 'msi': result['msi'],
                   'product_code': result['metadata']['product_code'], 'expected': expected}
        self.fixtures[fixture['product_code']] = fixture
        self.write_database_diagnostic(fixture)
        self.verify_fixture(fixture)
        return fixture

    @staticmethod
    def write_database_diagnostic(fixture):
        database = validator.ReadOnlyDatabase(fixture['msi'])
        try:
            names = {row['Name'] for row in database.rows('SELECT `Name` FROM `_Tables`')}
            diagnostic = {'read_only': True, 'template': database.template(), 'table_names': sorted(names), 'tables': {}}
            for name in ('Property', 'Directory', 'Component', 'Feature', 'FeatureComponents', 'File',
                         'Registry', 'RemoveFile', 'Shortcut', 'CustomAction', 'InstallExecuteSequence',
                         'InstallUISequence', 'Media', 'RegLocator', 'DrLocator', 'Signature', 'Upgrade'):
                if name in names:
                    diagnostic['tables'][name] = database.rows(f'SELECT * FROM `{name}`')
            if 'Binary' in names:
                diagnostic['tables']['Binary'] = database.rows('SELECT `Name` FROM `Binary`')
        finally:
            database.close()
        (fixture['msi'].parent / 'MSI-DATABASE.json').write_text(json.dumps(diagnostic, indent=4), encoding='utf-8')

    def verify_fixture(self, fixture):
        self.assertTrue(self.directory.resolve().is_relative_to((ROOT / 'work/installer-fixtures').resolve()))
        self.assertTrue(self.appdir.resolve().is_relative_to(self.directory.resolve()))
        for path in (self.directory, self.appdir):
            if path.exists():
                authoring._plain_path(path)
        self.assertTrue(fixture['msi'].resolve().is_relative_to(self.directory.resolve()))
        self.assertTrue(fixture['payload'].resolve().is_relative_to(self.directory.resolve()))
        self.assertNotEqual(self.identity.upgrade_code, authoring.PRODUCTION_IDENTITY.upgrade_code)
        self.assertEqual(self.identity.registry_key, 'SOFTWARE\\XXMIInstallerTests\\' + self.identifier)
        self.assertEqual(self.identity.product_name, 'XXMI Installer Fixture ' + self.identifier)
        self.assertEqual(self.identity.shortcut_name, 'XXMI Fixture ' + self.identifier)
        self.assertEqual(self.identity.default_directory_name, 'XXMI Fixture ' + self.identifier)
        self.assertEqual(self.identity.artifact_prefix, 'XXMI-Fixture-' + self.identifier)
        for name, data in fixture['expected'].items():
            self.assertTrue(data.startswith(INERT))
            self.assertEqual((fixture['payload'] / name).read_bytes(), data)
        receipt = validator.validate_msi(fixture['msi'], fixture['payload'], fixture['version'], self.identity)
        self.assertEqual(receipt['product_code'], fixture['product_code'])
        self.assertEqual(set(receipt['files']), set(fixture['expected']) | {authoring.MANIFEST})
        self.assertTrue(receipt['read_only'])
        (fixture['msi'].parent / 'FIXTURE-VALIDATION.json').write_text(json.dumps(receipt, indent=4), encoding='utf-8')

    def install(self, fixture, repair=False):
        require_high_integrity()
        self.verify_fixture(fixture)
        self.cleanup_codes.add(fixture['product_code'])
        arguments = [self.msiexec, '/i', fixture['msi'], '/qn', '/norestart',
                     'APPDIR=' + str(self.appdir), 'CREATE_SHORTCUTS=',
                     '/l*v', self.directory / (fixture['version'] + ('-repair' if repair else '-install') + '.log')]
        if repair:
            arguments += ['REINSTALL=ALL', 'REINSTALLMODE=amus']
        self.run_tool(arguments, fixture['version'] + ('-repair-command.log' if repair else '-install-command.log'), (0, 3010))

    def uninstall(self, code, cleanup=False):
        require_high_integrity()
        self.assertIn(code, self.fixtures)
        fixture = self.fixtures[code]
        self.verify_fixture(fixture)
        self.assertEqual(code, authoring._guid(self.identity, 'product:' + fixture['version'] + ':' + authoring.validate_payload(fixture['payload'], fixture['version'])['payload_sha256']))
        self.run_tool([self.msiexec, '/x', code, '/qn', '/norestart', '/l*v', self.directory / (fixture['version'] + '-uninstall.log')],
                      fixture['version'] + '-uninstall-command.log', (0, 1605, 3010) if cleanup else (0, 3010))
        self.cleanup_codes.discard(code)

    def cleanup_products(self):
        failures = []
        for code in list(self.cleanup_codes):
            try:
                self.uninstall(code, cleanup=True)
            except Exception as exception:
                failures.append(str(exception))
        self.assertFalse(failures, 'Isolated fixture cleanup failed: ' + '; '.join(failures))

    def assert_payload(self, fixture):
        for name, data in fixture['expected'].items():
            self.assertEqual((self.appdir / name).read_bytes(), data, name)
        self.assertEqual((self.appdir / authoring.MANIFEST).read_bytes(), (fixture['payload'] / authoring.MANIFEST).read_bytes())

    def assert_registry(self, installed):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self.identity.registry_key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                path = winreg.QueryValueEx(key, 'Path')[0]
        except FileNotFoundError:
            self.assertFalse(installed, 'Fixture installation path registry is missing')
        else:
            self.assertTrue(installed, 'Fixture installation path registry survived uninstall')
            self.assertEqual(Path(path).resolve(), self.appdir.resolve())

    @staticmethod
    def product_state(code):
        api = ctypes.WinDLL('msi', use_last_error=True)
        api.MsiQueryProductStateW.argtypes = [ctypes.c_wchar_p]
        api.MsiQueryProductStateW.restype = ctypes.c_int
        return api.MsiQueryProductStateW(code)

    def test_install_upgrade_repair_uninstall_preserve_unmanaged_data(self):
        first, second = self.build('1.0.0'), self.build('1.0.1')
        self.install(first)
        self.assert_payload(first)
        self.assert_registry(True)
        unmanaged = {name: INERT + name.encode('ascii') for name in (
            'XXMI Launcher Config.json', 'Mods/user.txt', 'Resources/Security/user.txt',
            'Locale/active_locale.cfg', 'Themes/user-theme.json')}
        for name, contents in unmanaged.items():
            path = self.appdir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        self.install(second)
        self.assertEqual(self.product_state(first['product_code']), -1, 'The prior fixture product must be removed by the major upgrade')
        self.assertEqual(self.product_state(second['product_code']), 5, 'The new fixture product must be installed')
        self.assert_payload(second)
        self.assertFalse((self.appdir / 'Themes/obsolete.txt').exists())
        self.assert_registry(True)
        for name, contents in unmanaged.items():
            self.assertEqual((self.appdir / name).read_bytes(), contents)
        (self.appdir / 'Resources/Bin/_internal/runtime.dll').write_bytes(b'corrupted managed fixture')
        self.install(second, repair=True)
        self.assert_payload(second)
        self.uninstall(second['product_code'])
        self.assert_registry(False)
        for name, contents in unmanaged.items():
            self.assertEqual((self.appdir / name).read_bytes(), contents, name)
        for name in second['expected']:
            self.assertFalse((self.appdir / name).exists(), name)
        self.assertFalse((self.appdir / authoring.MANIFEST).exists())
        (self.directory / 'LIFECYCLE-RESULT.json').write_text(json.dumps({
            'fixture_only': True, 'identity': self.identity.__dict__,
            'versions': ['1.0.0', '1.0.1'], 'upgrade': True, 'same_version_repair': True,
            'unmanaged_files_retained_after_upgrade_and_uninstall': sorted(unmanaged),
            'application_executed': False,
        }, indent=4), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
