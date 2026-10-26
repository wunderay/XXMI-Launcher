'''Original WiX 3 authoring for an already staged, verified launcher payload.

This module never expands source inputs or downloads tools. Identity overrides are
an import-only facility for isolated, inert Windows Installer qualification.
'''
import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
import xml.etree.ElementTree as ET


NAMESPACE = 'http://schemas.microsoft.com/wix/2006/wi'
ET.register_namespace('', NAMESPACE)
EXECUTABLE = 'Resources/Bin/XXMI Launcher.exe'
MANIFEST = 'LAUNCHER-BUILD.json'
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
MAX_PAYLOAD_FILES = 65535


@dataclass(frozen=True)
class Identity:
    product_name: str = 'XXMI Launcher (development)'
    manufacturer: str = 'XXMI Launcher contributors'
    upgrade_code: str = '{5EBB1A78-20AD-47CF-95E8-78BCEDBB4D93}'
    registry_key: str = r'SOFTWARE\SpectrumQT\XXMI Launcher'
    default_directory_name: str = 'XXMI Launcher'
    shortcut_name: str = 'XXMI Launcher'
    artifact_prefix: str = 'XXMI-Launcher-Installer-Online'


PRODUCTION_IDENTITY = Identity()


def validate_version(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('MSI version must have three canonical ASCII numeric components')
    parts = value.split('.')
    if any(len(part) > 5 for part in parts) or any(int(part) > bound for part, bound in zip(parts, (255, 255, 65535))):
        raise ValueError('MSI version bounds are major/minor 255 and build 65535')
    return value


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _plain_path(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError(f'Symlink/reparse point is forbidden: {path}')


def _relative_name(name: str) -> str:
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError(f'Noncanonical payload path: {name!r}')
    for part in name.split('/'):
        if part in ('', '.', '..') or part[-1:] in (' ', '.') or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            raise ValueError(f'Unsafe Windows payload path: {name!r}')
        if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.IGNORECASE):
            raise ValueError(f'Reserved Windows path: {name!r}')
    return name


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate manifest key: {key}')
        result[key] = value
    return result


def validate_payload(payload: Path, version: str) -> dict:
    version = validate_version(version)
    payload = Path(os.path.abspath(payload))
    for parent in (payload, *payload.parents):
        _plain_path(parent)
    if not payload.is_dir():
        raise ValueError('Payload must be a directory')
    names, actual = {}, {}
    total_bytes, total_files = 0, 0
    for directory, directories, files in os.walk(payload, followlinks=False):
        for leaf in sorted(directories + files):
            path = Path(directory) / leaf
            _plain_path(path)
            name = _relative_name(path.relative_to(payload).as_posix())
            folded = name.casefold()
            if folded in names:
                raise ValueError(f'Windows case collision: {names[folded]} and {name}')
            names[folded] = name
            if not path.resolve().is_relative_to(payload):
                raise ValueError(f'Payload entry escapes its root: {name}')
            if path.is_dir():
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError(f'Payload entry is not a regular file: {name}')
            total_files += 1
            total_bytes += path.stat().st_size
            if total_files > MAX_PAYLOAD_FILES or total_bytes > MAX_PAYLOAD_BYTES:
                raise ValueError('Payload exceeds single-cabinet limits (512 MiB / 65535 files)')
            if name != MANIFEST:
                if name != 'LICENSE' and not any(name.startswith(prefix) for prefix in ('Resources/Bin/', 'Themes/', 'Locale/')):
                    raise ValueError(f'File outside staged application layout: {name}')
                actual[name] = digest(path)
    manifest_path = payload / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'), object_pairs_hook=_unique_json)
    if manifest.get('format') != 1 or manifest.get('version') != version:
        raise ValueError('Payload manifest format/version does not match the requested MSI')
    if manifest.get('source_input_status') != '' or manifest.get('source_has_local_changes', False) is not False:
        raise ValueError('Payload source inputs must be clean')
    if not isinstance(manifest.get('launcher_source'), str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', manifest['launcher_source']):
        raise ValueError('Payload must identify its exact launcher source commit')
    expected = manifest.get('files')
    if not isinstance(expected, dict) or not expected:
        raise ValueError('Payload manifest files must be a nonempty hash map')
    for name, value in expected.items():
        _relative_name(name)
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError(f'Invalid SHA-256 for {name}')
    if actual != expected:
        raise ValueError('Payload file set or SHA-256 differs from its manifest')
    if EXECUTABLE not in actual or 'LICENSE' not in actual or not all(any(name.startswith(prefix) for name in actual) for prefix in ('Resources/Bin/_internal/', 'Themes/', 'Locale/')):
        raise ValueError('Missing required staged executable, runtime, Themes, Locale, or LICENSE')
    actual[MANIFEST] = digest(manifest_path)
    plan = dict(sorted(actual.items()))
    fingerprint = hashlib.sha256(json.dumps(plan, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
    return {'payload': str(payload), 'version': version, 'launcher_source': manifest['launcher_source'],
            'source_input_status': '', 'files': plan, 'payload_sha256': fingerprint}


def _guid(identity: Identity, name: str) -> str:
    return '{' + str(uuid.uuid5(uuid.UUID(identity.upgrade_code), name.casefold())).upper() + '}'


def _id(prefix: str, name: str) -> str:
    return prefix + hashlib.sha256(name.casefold().encode('utf-8')).hexdigest()[:24]


def _element(parent, tag, **attributes):
    return ET.SubElement(parent, '{' + NAMESPACE + '}' + tag, attributes)


def _rtf(text: str) -> str:
    body = []
    for character in text:
        if character in '\\{}':
            body.append('\\' + character)
        elif character == '\n':
            body.append('\\par\n')
        elif ord(character) < 128:
            body.append(character)
        else:
            for offset in range(0, len(character.encode('utf-16-le')), 2):
                number = int.from_bytes(character.encode('utf-16-le')[offset:offset + 2], 'little', signed=True)
                body.append(f'\\u{number}?')
    return '{\\rtf1\\ansi\\uc1 ' + ''.join(body) + '}\n'


def generate_source(payload: Path, version: str, output_dir: Path, identity: Identity | None = None) -> dict:
    identity = identity or PRODUCTION_IDENTITY
    uuid.UUID(identity.upgrade_code)
    if identity != PRODUCTION_IDENTITY:
        if identity.upgrade_code.upper() == PRODUCTION_IDENTITY.upgrade_code.upper() or identity.registry_key.casefold() == PRODUCTION_IDENTITY.registry_key.casefold():
            raise ValueError('Test identities must isolate both UpgradeCode and registry key')
    for name in (identity.default_directory_name, identity.shortcut_name, identity.artifact_prefix):
        _relative_name(name)
        if '/' in name:
            raise ValueError('Identity names must be single Windows path segments')
    plan = validate_payload(payload, version)
    payload = Path(plan['payload'])
    output_dir = Path(output_dir).resolve()
    if output_dir.is_relative_to(payload):
        raise ValueError('Authoring output must be outside the immutable payload')
    output_dir.mkdir(parents=True, exist_ok=True)
    product_code = _guid(identity, 'product:' + version + ':' + plan['payload_sha256'])
    wix = ET.Element('{' + NAMESPACE + '}Wix')
    product = _element(wix, 'Product', Id=product_code, Name=identity.product_name, Language='1033',
                       Version=version, Manufacturer=identity.manufacturer, UpgradeCode=identity.upgrade_code)
    _element(product, 'Package', Id='*', InstallerVersion='500', Compressed='yes', InstallScope='perMachine', Platform='x64',
             Description=identity.product_name + ' contributor development installer', Manufacturer=identity.manufacturer)
    _element(product, 'MajorUpgrade', AllowSameVersionUpgrades='yes', DowngradeErrorMessage='A newer version is already installed.', Schedule='afterInstallInitialize')
    _element(product, 'Media', Id='1', Cabinet='launcher.cab', EmbedCab='yes', CompressionLevel='mszip')
    _element(product, 'Property', Id='APPDIR', Secure='yes')
    _element(product, 'Property', Id='CREATE_SHORTCUTS', Value='1', Secure='yes')
    _element(product, 'Property', Id='MSIRESTARTMANAGERCONTROL', Value='Disable')
    existing = _element(product, 'Property', Id='EXISTINGAPPDIR', Secure='yes')
    _element(existing, 'RegistrySearch', Id='ExistingInstallPath', Root='HKLM', Key=identity.registry_key,
             Name='Path', Type='raw', Win64='yes')
    _element(product, 'SetProperty', Id='APPDIR', Value='[EXISTINGAPPDIR]', After='AppSearch', Sequence='both').text = 'NOT APPDIR AND EXISTINGAPPDIR'
    previous_shortcut = _element(product, 'Property', Id='PREVIOUSDESKTOP', Secure='yes')
    _element(previous_shortcut, 'RegistrySearch', Id='ExistingDesktopMarker', Root='HKCU', Key=identity.registry_key,
             Name='DesktopShortcut', Type='raw', Win64='yes')
    previous_link = _element(product, 'Property', Id='PREVIOUSDESKTOPLINK', Secure='yes')
    link_directory = _element(previous_link, 'DirectorySearch', Id='ExistingDesktopDirectory', Path='[DesktopFolder]', Depth='0')
    _element(link_directory, 'FileSearch', Id='ExistingDesktopLink', Name=identity.shortcut_name + '.lnk', Type='file')
    _element(product, 'Property', Id='WIXUI_INSTALLDIR', Value='APPDIR')
    _element(product, 'UIRef', Id='WixUI_InstallDir')
    license_path = output_dir / 'installer-license.rtf'
    license_path.write_text(_rtf(identity.product_name + '\nBuilt by ' + identity.manufacturer + '.\n\n' + (payload / 'LICENSE').read_text(encoding='utf-8')), encoding='ascii')
    _element(product, 'WixVariable', Id='WixUILicenseRtf', Value=str(license_path))
    root = _element(product, 'Directory', Id='TARGETDIR', Name='SourceDir')
    app_data = _element(root, 'Directory', Id='AppDataFolder')
    app = _element(app_data, 'Directory', Id='APPDIR', Name=identity.default_directory_name)
    _element(root, 'Directory', Id='DesktopFolder')
    _element(root, 'Directory', Id='ProgramMenuFolder')
    directories = {'': app}
    main_feature = _element(product, 'Feature', Id='Main', Title=identity.product_name, Level='1', Absent='disallow')
    file_plan = []
    for name, sha256 in plan['files'].items():
        relative = Path(name)
        parent = ''
        for part in relative.parts[:-1]:
            current = parent + '/' + part if parent else part
            if current not in directories:
                directories[current] = _element(directories[parent], 'Directory', Id=_id('Dir', current), Name=part)
            parent = current
        component_id = _id('Cmp', name)
        component_guid = _guid(identity, 'file:' + name)
        component = _element(directories[parent], 'Component', Id=component_id, Guid=component_guid, Win64='yes')
        file = _element(component, 'File', Id=_id('File', name), Name=relative.name, Source=str(payload / relative), DiskId='1', Compressed='yes')
        _element(component, 'RegistryValue', Root='HKCU', Key=identity.registry_key + r'\InstallerFiles',
                 Name=component_id, Type='integer', Value='1', KeyPath='yes')
        _element(main_feature, 'ComponentRef', Id=component_id)
        file_plan.append({'path': name, 'sha256': sha256, 'component_id': component_id, 'component_guid': component_guid})
    registry = _element(root, 'Component', Id='InstallRegistry', Guid=_guid(identity, 'registry:path'), Win64='yes')
    _element(registry, 'RegistryValue', Root='HKLM', Key=identity.registry_key, Name='Path', Type='string', Value='[APPDIR]', KeyPath='yes')
    _element(main_feature, 'ComponentRef', Id='InstallRegistry')
    cleanup = _element(app, 'Component', Id='ManagedDirectories', Guid=_guid(identity, 'directories:empty'), Win64='yes')
    _element(cleanup, 'RegistryValue', Root='HKCU', Key=identity.registry_key, Name='InstallerDirectories', Type='integer', Value='1', KeyPath='yes')
    for name, directory in directories.items():
        _element(cleanup, 'RemoveFolder', Id=_id('RemoveDir', name), Directory=directory.attrib['Id'], On='uninstall')
    _element(main_feature, 'ComponentRef', Id='ManagedDirectories')
    desktop = _element(app, 'Component', Id='DesktopShortcutComponent', Guid=_guid(identity, 'shortcut:desktop'), Win64='yes')
    _element(desktop, 'Condition').text = 'CREATE_SHORTCUTS = "1" OR ((NOT CREATE_SHORTCUTS) AND (PREVIOUSDESKTOP OR PREVIOUSDESKTOPLINK))'
    _element(desktop, 'Shortcut', Id='StartMenuShortcut', Directory='ProgramMenuFolder', Name=identity.shortcut_name,
             Target='[APPDIR]Resources\\Bin\\XXMI Launcher.exe', WorkingDirectory='APPDIR')
    _element(desktop, 'Shortcut', Id='DesktopShortcut', Directory='DesktopFolder', Name=identity.shortcut_name,
             Target='[APPDIR]Resources\\Bin\\XXMI Launcher.exe', WorkingDirectory='APPDIR')
    _element(desktop, 'RegistryValue', Root='HKCU', Key=identity.registry_key, Name='DesktopShortcut', Type='integer', Value='1', KeyPath='yes')
    _element(main_feature, 'ComponentRef', Id='DesktopShortcutComponent')
    source = output_dir / 'installer.wxs'
    ET.indent(wix, space='    ')
    ET.ElementTree(wix).write(source, encoding='utf-8', xml_declaration=True)
    receipt = dict(plan, format=1, identity=asdict(identity), product_code=product_code, component_plan=file_plan,
                   authoring_sha256=digest(Path(__file__)), wxs_sha256=digest(source),
                   msi_name=f'{identity.artifact_prefix}-v{version}.msi', signed=False,
                   qualification='Development authoring only; Windows install/upgrade/uninstall qualification is separate.')
    receipt_path = output_dir / 'INSTALLER-AUTHORING.json'
    receipt_path.write_text(json.dumps(receipt, indent=4) + '\n', encoding='utf-8')
    return {'source': source, 'receipt': receipt_path, 'license': license_path,
            'msi': output_dir / receipt['msi_name'], 'metadata': receipt}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output-directory', type=Path)
    parser.add_argument('--check-receipt', type=Path)
    args = parser.parse_args()
    if args.check_receipt:
        plan = validate_payload(args.payload, args.version)
        receipt = json.loads(args.check_receipt.read_text(encoding='utf-8'))
        if any(receipt.get(key) != plan[key] for key in ('payload', 'version', 'launcher_source', 'payload_sha256', 'files')):
            raise ValueError('Payload changed after authoring')
    elif args.output_directory:
        result = generate_source(args.payload, args.version, args.output_directory)
        print(result['source'])
    else:
        parser.error('--output-directory or --check-receipt is required')


if __name__ == '__main__':
    main()
