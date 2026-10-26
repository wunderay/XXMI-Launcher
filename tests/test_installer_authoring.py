'''Original MSI authoring validation without invoking Windows or executing a payload.'''
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('installer_authoring', ROOT / 'tools/Installer/authoring.py')
authoring = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(authoring)
NS = {'w': authoring.NAMESPACE}


def fixture_payload(directory, version='2.4.10'):
    payload = directory / 'install-payload'
    files = {
        'Resources/Bin/XXMI Launcher.exe': b'inert fixture; never execute',
        'Resources/Bin/_internal/runtime.dll': b'inert runtime',
        'Themes/Default/theme.json': b'{}',
        'Locale/en/strings.json': b'{}',
        'LICENSE': b'Fixture license {text} \\ unicode: \xc3\xa9',
    }
    for name, data in files.items():
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {'format': 1, 'version': version, 'launcher_source': 'a' * 40,
                'source_input_status': '', 'source_has_local_changes': False,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (payload / 'LAUNCHER-BUILD.json').write_text(json.dumps(manifest), encoding='utf-8')
    return payload


class InstallerAuthoringTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='original MSI authoring ')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.payload = fixture_payload(self.directory)

    def manifest(self, mutate):
        path = self.payload / 'LAUNCHER-BUILD.json'
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data), encoding='utf-8')

    def generate(self, **kwargs):
        return authoring.generate_source(self.payload, '2.4.10', self.directory / 'authoring', **kwargs)

    def test_version_canonical_and_msi_bounds(self):
        for version in ('0.0.0', '2.4.10', '255.255.65535'):
            self.assertEqual(authoring.validate_version(version), version)
        for version in ('256.0.0', '1.256.0', '1.2.65536', '01.2.3', '1.2', '1.2.3.4',
                        '1.2.3dev', '1.2.٣', '1.2.3\n', '-1.2.3', '999999999999999999999.2.3'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                authoring.validate_version(version)

    def test_receipt_binds_exact_payload_and_source(self):
        result = self.generate()
        receipt = json.loads(result['receipt'].read_text())
        self.assertEqual(receipt['launcher_source'], 'a' * 40)
        self.assertEqual(receipt['source_input_status'], '')
        self.assertEqual(receipt['wxs_sha256'], authoring.digest(result['source']))
        self.assertEqual(receipt['files'], authoring.validate_payload(self.payload, '2.4.10')['files'])
        self.assertIn('LAUNCHER-BUILD.json', receipt['files'])
        self.assertEqual(result['msi'].name, 'XXMI-Launcher-Installer-Online-v2.4.10.msi')
        self.assertFalse(receipt['signed'])
        self.assertIn('\\u233?', result['license'].read_text())

    def test_file_drift_and_extra_files_fail(self):
        (self.payload / 'LICENSE').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.generate()
        (self.payload / 'LICENSE').unlink()
        with self.assertRaises(ValueError):
            self.generate()

    def test_unmanaged_sources_and_security_material_cannot_enter_msi(self):
        for name in ('launcher.ini', 'Resources/Security/key.pem', 'Mods/anything.txt'):
            with self.subTest(name=name):
                extra = self.payload / name
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_text('unmanaged')
                with self.assertRaisesRegex(ValueError, 'outside staged'):
                    self.generate()
                extra.unlink()

    def test_extra_file_inside_allowed_directory_fails_exact_manifest(self):
        (self.payload / 'Themes/extra.txt').write_text('not declared')
        with self.assertRaisesRegex(ValueError, 'file set'):
            self.generate()

    def test_version_source_cleanliness_and_commit_are_required(self):
        for key, value in (('version', '2.4.11'), ('source_input_status', ' M src/app.py'),
                           ('source_has_local_changes', True), ('launcher_source', 'unknown'), ('format', 2)):
            original = (self.payload / 'LAUNCHER-BUILD.json').read_text()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.manifest(lambda data: data.update({key: value}))
                self.generate()
            (self.payload / 'LAUNCHER-BUILD.json').write_text(original)

    def test_invalid_manifest_paths_and_hashes_rejected(self):
        for name in ('../outside', '/absolute', 'C:/absolute', 'Themes\\bad', 'Themes/CON.txt',
                     'Themes/trailing.', 'Themes/trailing ', 'Themes//bad', 'Themes/a:stream'):
            original = (self.payload / 'LAUNCHER-BUILD.json').read_text()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.manifest(lambda data: data['files'].update({name: '0' * 64}))
                self.generate()
            (self.payload / 'LAUNCHER-BUILD.json').write_text(original)
        self.manifest(lambda data: data['files'].update({'LICENSE': 'not a hash'}))
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            self.generate()

    def test_windows_case_collisions_rejected_for_files_and_directories(self):
        for extra_name in ('Themes/Default/THEME.json', 'themes/another.txt'):
            extra = self.payload / extra_name
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text('collision')
            with self.subTest(extra=extra_name), self.assertRaisesRegex(ValueError, 'case collision'):
                self.generate()
            extra.unlink()
            if extra_name.startswith('themes/'):
                extra.parent.rmdir()

    def test_symlink_file_directory_and_payload_root_rejected(self):
        outside = self.directory / 'outside'
        outside.write_text('outside')
        link = self.payload / 'Themes/link'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlink creation unavailable')
        with self.assertRaisesRegex(ValueError, 'Symlink/reparse'):
            self.generate()
        link.unlink()
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink/reparse'):
            self.generate()
        link.unlink()
        root_link = self.directory / 'linked-payload'
        root_link.symlink_to(self.payload, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink/reparse'):
            authoring.generate_source(root_link, '2.4.10', self.directory / 'output')

    def test_single_cabinet_limits_are_enforced_before_compilation(self):
        for constant, value in (('MAX_PAYLOAD_BYTES', 1), ('MAX_PAYLOAD_FILES', 1)):
            with self.subTest(limit=constant), patch.object(authoring, constant, value):
                with self.assertRaisesRegex(ValueError, 'single-cabinet limits'):
                    self.generate()

    def test_duplicate_json_keys_rejected(self):
        path = self.payload / 'LAUNCHER-BUILD.json'
        path.write_text(path.read_text().replace('"format": 1', '"format": 1, "format": 1'))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.generate()

    def test_required_layout_and_output_separation(self):
        self.manifest(lambda data: data['files'].pop('Resources/Bin/_internal/runtime.dll'))
        (self.payload / 'Resources/Bin/_internal/runtime.dll').unlink()
        with self.assertRaisesRegex(ValueError, 'required staged'):
            self.generate()
        self.payload = fixture_payload(self.directory)
        with self.assertRaisesRegex(ValueError, 'outside the immutable payload'):
            authoring.generate_source(self.payload, '2.4.10', self.payload / 'output')

    def test_component_guids_stable_but_product_binds_payload(self):
        first = self.generate()['metadata']
        executable = self.payload / 'Resources/Bin/XXMI Launcher.exe'
        executable.write_bytes(b'inert changed payload')
        self.manifest(lambda data: data['files'].update({authoring.EXECUTABLE: authoring.digest(executable)}))
        second = self.generate()['metadata']
        self.assertEqual([item['component_guid'] for item in first['component_plan']],
                         [item['component_guid'] for item in second['component_plan']])
        self.assertNotEqual(first['product_code'], second['product_code'])
        self.manifest(lambda data: data.update(version='2.4.11'))
        third = authoring.generate_source(self.payload, '2.4.11', self.directory / 'new-version')['metadata']
        self.assertEqual([item['component_guid'] for item in second['component_plan']],
                         [item['component_guid'] for item in third['component_plan']])
        self.assertNotEqual(second['product_code'], third['product_code'])

    def test_fixture_identity_isolated_from_production_and_receipted(self):
        identity = replace(authoring.PRODUCTION_IDENTITY, product_name='Inert MSI qualification',
                           upgrade_code='{EF6EACDB-E334-4958-9617-1B2452000625}',
                           registry_key=r'SOFTWARE\XXMIInertQualification', shortcut_name='Inert qualification')
        metadata = self.generate(identity=identity)['metadata']
        self.assertEqual(metadata['identity']['registry_key'], identity.registry_key)
        self.assertEqual(metadata['identity']['upgrade_code'], identity.upgrade_code)
        for unsafe in (replace(identity, registry_key=authoring.PRODUCTION_IDENTITY.registry_key),
                       replace(identity, upgrade_code=authoring.PRODUCTION_IDENTITY.upgrade_code)):
            with self.subTest(identity=unsafe), self.assertRaisesRegex(ValueError, 'isolate'):
                self.generate(identity=unsafe)

    def test_upgrade_preserves_unmanaged_files_and_uses_standard_wix_ui(self):
        tree = ET.parse(self.generate()['source'])
        product = tree.find('w:Product', NS)
        self.assertEqual(product.attrib['UpgradeCode'], authoring.PRODUCTION_IDENTITY.upgrade_code)
        package = tree.find('.//w:Package', NS)
        self.assertEqual(package.attrib['InstallScope'], 'perMachine')
        self.assertEqual(package.attrib['Platform'], 'x64')
        media = tree.find('.//w:Media', NS)
        self.assertEqual(media.attrib, {'Id': '1', 'Cabinet': 'launcher.cab', 'EmbedCab': 'yes', 'CompressionLevel': 'mszip'})
        self.assertTrue(all(file.attrib['DiskId'] == '1' for file in tree.findall('.//w:File', NS)))
        self.assertEqual(tree.find('.//w:MajorUpgrade', NS).attrib['AllowSameVersionUpgrades'], 'yes')
        self.assertEqual(tree.find('.//w:UIRef', NS).attrib['Id'], 'WixUI_InstallDir')
        self.assertEqual(tree.find('.//w:Directory[@Id="APPDIR"]', NS).attrib['Name'], 'XXMI Launcher')
        search = tree.find('.//w:RegistrySearch[@Id="ExistingInstallPath"]', NS)
        self.assertEqual(search.attrib['Root'], 'HKLM')
        self.assertEqual(search.attrib['Win64'], 'yes')
        action = tree.find('.//w:SetProperty', NS)
        self.assertEqual(action.text, 'NOT APPDIR AND EXISTINGAPPDIR')
        self.assertEqual(action.attrib['Sequence'], 'both')
        self.assertFalse(tree.findall('.//w:CustomAction', NS))
        self.assertFalse(tree.findall('.//w:RemoveFile', NS))
        self.assertFalse(tree.findall('.//w:RemoveFolderEx', NS))
        removals = tree.findall('.//w:RemoveFolder', NS)
        self.assertTrue(removals)
        self.assertTrue(all(item.attrib['On'] == 'uninstall' for item in removals))
        self.assertEqual(len(tree.findall('.//w:File', NS)), 6)
        registry = tree.find('.//w:Component[@Id="InstallRegistry"]/w:RegistryValue', NS)
        self.assertEqual(registry.attrib['Root'], 'HKLM')
        self.assertEqual(registry.attrib['Value'], '[APPDIR]')
        shortcuts = tree.find('.//w:Component[@Id="DesktopShortcutComponent"]', NS)
        condition = shortcuts.find('w:Condition', NS).text
        self.assertIn('CREATE_SHORTCUTS = "1"', condition)
        self.assertIn('NOT CREATE_SHORTCUTS', condition)
        self.assertIn('PREVIOUSDESKTOPLINK', condition)
        self.assertEqual(len(shortcuts.findall('w:Shortcut', NS)), 2)
        for component in tree.findall('.//w:Component', NS):
            self.assertEqual(component.attrib['Win64'], 'yes')
            if component.attrib['Id'] != 'InstallRegistry':
                key = component.find('w:RegistryValue[@KeyPath="yes"]', NS)
                self.assertEqual(key.attrib['Root'], 'HKCU')


if __name__ == '__main__':
    unittest.main()
