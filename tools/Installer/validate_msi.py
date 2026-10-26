'''Read an MSI database and its embedded cabinets; never install or execute it.'''
import argparse
import ctypes
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parent))
import authoring


class ReadOnlyDatabase:
    '''Use only the read-only Windows Installer database API, with owned handles.'''

    def __init__(self, path):
        if os.name != 'nt':
            raise RuntimeError('Reading compiled MSI databases requires Windows')
        from ctypes import wintypes
        self.api = ctypes.WinDLL('msi', use_last_error=True)
        handle = wintypes.UINT
        definitions = {
            'MsiOpenDatabaseW': ([wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(handle)], wintypes.UINT),
            'MsiDatabaseOpenViewW': ([handle, wintypes.LPCWSTR, ctypes.POINTER(handle)], wintypes.UINT),
            'MsiViewExecute': ([handle, handle], wintypes.UINT),
            'MsiViewFetch': ([handle, ctypes.POINTER(handle)], wintypes.UINT),
            'MsiViewGetColumnInfo': ([handle, ctypes.c_int, ctypes.POINTER(handle)], wintypes.UINT),
            'MsiRecordGetFieldCount': ([handle], wintypes.UINT),
            'MsiRecordGetStringW': ([handle, wintypes.UINT, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.UINT),
            'MsiRecordReadStream': ([handle, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)], wintypes.UINT),
            'MsiGetSummaryInformationW': ([handle, wintypes.LPCWSTR, wintypes.UINT, ctypes.POINTER(handle)], wintypes.UINT),
            'MsiSummaryInfoGetPropertyW': ([handle, wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(ctypes.c_int), ctypes.c_void_p, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.UINT),
            'MsiCloseHandle': ([handle], wintypes.UINT),
        }
        for name, (arguments, result) in definitions.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = handle()
        self._check(self.api.MsiOpenDatabaseW(str(Path(path).resolve()), None, ctypes.byref(self.handle)))

    @staticmethod
    def _check(code):
        if code:
            raise ValueError(f'Read-only MSI database operation failed: {code}')

    def close(self):
        if self.handle.value:
            self.api.MsiCloseHandle(self.handle)
            self.handle.value = 0

    def _string(self, record, index):
        size = ctypes.c_ulong(0)
        code = self.api.MsiRecordGetStringW(record, index, None, ctypes.byref(size))
        if code not in (0, 234):
            self._check(code)
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        size.value += 1
        self._check(self.api.MsiRecordGetStringW(record, index, buffer, ctypes.byref(size)))
        return buffer.value

    def _view(self, query):
        view = ctypes.c_uint()
        self._check(self.api.MsiDatabaseOpenViewW(self.handle, query, ctypes.byref(view)))
        try:
            self._check(self.api.MsiViewExecute(view, 0))
        except BaseException:
            self.api.MsiCloseHandle(view)
            raise
        return view

    def rows(self, query):
        view = self._view(query)
        try:
            columns = ctypes.c_uint()
            self._check(self.api.MsiViewGetColumnInfo(view, 0, ctypes.byref(columns)))
            try:
                names = [self._string(columns, index) for index in range(1, self.api.MsiRecordGetFieldCount(columns) + 1)]
            finally:
                self.api.MsiCloseHandle(columns)
            result = []
            while True:
                record = ctypes.c_uint()
                code = self.api.MsiViewFetch(view, ctypes.byref(record))
                if code == 259:
                    return result
                self._check(code)
                try:
                    result.append(dict(zip(names, (self._string(record, index) for index in range(1, len(names) + 1)))))
                finally:
                    self.api.MsiCloseHandle(record)
        finally:
            self.api.MsiCloseHandle(view)

    def stream(self, name, limit):
        if not re.fullmatch(r'[A-Za-z0-9_.]+', name):
            raise ValueError('Unsafe embedded cabinet name')
        view = self._view(f"SELECT `Data` FROM `_Streams` WHERE `Name` = '{name}'")
        record = ctypes.c_uint()
        try:
            self._check(self.api.MsiViewFetch(view, ctypes.byref(record)))
            result = bytearray()
            while True:
                buffer = ctypes.create_string_buffer(65536)
                size = ctypes.c_ulong(len(buffer))
                self._check(self.api.MsiRecordReadStream(record, 1, buffer, ctypes.byref(size)))
                if not size.value:
                    return bytes(result)
                result.extend(buffer.raw[:size.value])
                require(len(result) <= limit, 'Embedded cabinet exceeds the payload size bound')
        finally:
            if record.value:
                self.api.MsiCloseHandle(record)
            self.api.MsiCloseHandle(view)

    def template(self):
        summary = ctypes.c_uint()
        self._check(self.api.MsiGetSummaryInformationW(self.handle, None, 0, ctypes.byref(summary)))
        try:
            kind, integer = ctypes.c_uint(), ctypes.c_int()
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            self._check(self.api.MsiSummaryInfoGetPropertyW(summary, 7, ctypes.byref(kind), ctypes.byref(integer), None, buffer, ctypes.byref(size)))
            return buffer.value
        finally:
            self.api.MsiCloseHandle(summary)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cabinet_hashes(data, expected_sizes):
    '''Decode only single-cabinet NONE/MSZIP data, entirely in memory.

    Cabinet names must be MSI File identifiers. No cabinet path is ever written
    to disk, and decompressed bytes are bounded by the verified staged payload.
    '''
    require(len(data) >= 36, 'Truncated cabinet header')
    header = struct.unpack_from('<4sIIIIIBBHHHHH', data)
    signature, _, total, _, files_offset, _, minor, major, folder_count, file_count, flags, _, index = header
    require(signature == b'MSCF' and total == len(data) and (major, minor) == (1, 3) and all(header[index] == 0 for index in (1, 3, 5)), 'Invalid cabinet header')
    require(flags == 0 and index == 0 and 0 < folder_count <= file_count, 'Spanned/reserved cabinets are unsupported')
    require(file_count <= len(expected_sizes), 'Unexpected cabinet file count')
    require(36 + folder_count * 8 <= files_offset <= len(data), 'Invalid cabinet folder table')
    folders = [struct.unpack_from('<IHH', data, 36 + index * 8) for index in range(folder_count)]
    files, position = [], files_offset
    seen = set()
    for _ in range(file_count):
        require(position + 16 <= len(data), 'Truncated cabinet file table')
        size, offset, folder, _, _, _ = struct.unpack_from('<IIHHHH', data, position)
        position += 16
        end = data.find(b'\0', position)
        require(end != -1, 'Unterminated cabinet file name')
        name = data[position:end].decode('ascii')
        position = end + 1
        require(name in expected_sizes and name not in seen and size == expected_sizes[name], 'Unexpected cabinet file/name/size')
        require(folder < folder_count, 'Spanned cabinet files are unsupported')
        seen.add(name)
        files.append((name, size, offset, folder))
    result = {}
    for folder_index, (offset, block_count, compression) in enumerate(folders):
        require(compression in (0, 1), 'Only uncompressed/MSZIP cabinets are permitted')
        require(position <= offset <= len(data), 'Invalid cabinet data offset')
        members = [entry for entry in files if entry[3] == folder_index]
        bound = sum(entry[1] for entry in members)
        output = bytearray()
        cursor = offset
        for _ in range(block_count):
            require(cursor + 8 <= len(data), 'Truncated cabinet data header')
            _, compressed_size, unpacked_size = struct.unpack_from('<IHH', data, cursor)
            cursor += 8
            require(unpacked_size <= 32768 and cursor + compressed_size <= len(data), 'Invalid cabinet data length')
            block = data[cursor:cursor + compressed_size]
            cursor += compressed_size
            if compression == 1:
                require(block[:2] == b'CK', 'Invalid MSZIP marker')
                decoder = zlib.decompressobj(-15, zdict=bytes(output[-32768:])) if output else zlib.decompressobj(-15)
                block = decoder.decompress(block[2:], unpacked_size + 1)
                require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, 'Invalid MSZIP block')
            require(len(block) == unpacked_size and len(output) + len(block) <= bound, 'Cabinet decompression exceeds file bounds')
            output.extend(block)
        require(len(output) == bound, 'Cabinet decompressed size does not match its files')
        expected_offset = 0
        for name, size, file_offset, _ in sorted(members, key=lambda entry: entry[2]):
            require(file_offset == expected_offset and file_offset + size <= len(output), 'Cabinet files overlap or leave gaps')
            result[name] = hashlib.sha256(memoryview(output)[file_offset:file_offset + size]).hexdigest()
            expected_offset += size
    return result


ALLOWED_TABLES = set('''_Validation ActionText AdminExecuteSequence AdminUISequence AdvtExecuteSequence
    AppSearch Binary CheckBox ComboBox Component Condition Control ControlCondition ControlEvent CustomAction
    Dialog Directory DrLocator Error EventMapping Feature FeatureComponents File InstallExecuteSequence
    InstallUISequence LaunchCondition ListBox ListView Media MsiFileHash Property RadioButton Registry
    RegLocator RemoveFile Shortcut Signature TextStyle UIText Upgrade'''.split())


def validate_ui_binaries(rows):
    # Official WiX 3.14.1 Common.wxs names the folder icons Bmp_New/Bmp_Up.
    # https://github.com/wixtoolset/wix3/blob/wix3141rtm/src/ext/UIExtension/wixlib/Common.wxs
    allowed = {'WixUIWixca', 'WixUI_Bmp_Banner', 'WixUI_Bmp_Dialog', 'WixUI_Ico_Info',
               'WixUI_Ico_Exclam', 'WixUI_Bmp_New', 'WixUI_Bmp_Up'}
    unexpected = sorted({row['Name'] for row in rows} - allowed)
    require(not unexpected, f'Unexpected embedded action/UI binary IDs: {unexpected}')


def validate_tables(tables, template, plan, identity):
    '''Validate a data-only snapshot, also usable by portable regression tests.'''
    for table, rows in tables.items():
        require(not rows or table in ALLOWED_TABLES, f'Unexpected MSI side-effect table: {table}')
    properties = {row['Property']: row['Value'] for row in tables.get('Property', [])}
    expected_properties = {'ALLUSERS': '1', 'ProductVersion': plan['version'], 'UpgradeCode': identity.upgrade_code,
                           'ProductName': identity.product_name, 'Manufacturer': identity.manufacturer,
                           'MSIRESTARTMANAGERCONTROL': 'Disable', 'WIXUI_INSTALLDIR': 'APPDIR', 'CREATE_SHORTCUTS': '1'}
    require(all(properties.get(key) == value for key, value in expected_properties.items()), 'MSI identity/scope/version properties differ')
    require('MSIINSTALLPERUSER' not in properties and template.split(';')[0] == 'x64', 'MSI must be an x64 machine installation')
    require(properties.get('ProductCode') == authoring._guid(identity, 'product:' + plan['version'] + ':' + plan['payload_sha256']), 'Unexpected MSI ProductCode')
    require(set(('APPDIR', 'CREATE_SHORTCUTS', 'EXISTINGAPPDIR')).issubset(properties.get('SecureCustomProperties', '').split(';')), 'Installation properties must be secure')
    directories = {row['Directory']: row for row in tables.get('Directory', [])}
    require(directories.get('APPDIR', {}).get('Directory_Parent') == 'AppDataFolder', 'APPDIR must use the application data installation root')
    require(directories.get('APPDIR', {}).get('DefaultDir', '').split('|')[-1] == identity.default_directory_name, 'Unexpected APPDIR name')
    components = {row['Component']: row for row in tables.get('Component', [])}
    require(all(int(row['Attributes']) & 256 and not int(row['Attributes']) & (16 | 32 | 64 | 128) for row in components.values()), 'Components must be x64, managed, nonshared, and nonpermanent')
    require(len(components) == len(plan['files']) + 3, 'Unexpected component set')
    require(set(components) == {authoring._id('Cmp', name) for name in plan['files']} | {'InstallRegistry', 'ManagedDirectories', 'DesktopShortcutComponent'}, 'Unexpected MSI component identities')
    features = tables.get('Feature', [])
    require(len(features) == 1 and features[0]['Feature'] == 'Main' and features[0]['Level'] == '1', 'Unexpected application feature')
    require(len(tables.get('FeatureComponents', [])) == len(components) and {(row['Feature_'], row['Component_']) for row in tables['FeatureComponents']} == {('Main', name) for name in components}, 'Application feature must contain every managed component')
    registry = tables.get('Registry', [])
    require(len(registry) == len(plan['files']) + 3, 'Unexpected registry writes')
    by_component = {row['Component_']: row for row in registry}

    def relative_directory(directory):
        parts, visited = [], set()
        while directory != 'APPDIR':
            require(directory in directories and directory not in visited, 'File directory escapes APPDIR')
            visited.add(directory)
            row = directories[directory]
            name = row['DefaultDir'].split(':')[0].split('|')[-1]
            authoring._relative_name(name)
            require('/' not in name, 'Unsafe MSI directory segment')
            parts.append(name)
            directory = row['Directory_Parent']
        return '/'.join(reversed(parts))

    file_map = {}
    sizes = {}
    for row in tables.get('File', []):
        component = components.get(row['Component_'], {})
        parent = relative_directory(component.get('Directory_', ''))
        filename = row['FileName'].split('|')[-1]
        authoring._relative_name(filename)
        require('/' not in filename, 'Unsafe MSI file segment')
        name = parent + '/' + filename if parent else filename
        require(name in plan['files'] and name not in file_map.values(), 'Unexpected/duplicate installed file')
        require(row['File'] == authoring._id('File', name) and row['Component_'] == authoring._id('Cmp', name), 'Unexpected file/component identity')
        marker = by_component.get(row['Component_'], {})
        require((marker.get('Root'), marker.get('Key'), marker.get('Name'), marker.get('Value')) == ('1', identity.registry_key + r'\InstallerFiles', row['Component_'], '#1'), 'Unexpected file component registry marker')
        require(component['ComponentId'] == authoring._guid(identity, 'file:' + name) and component['KeyPath'] == marker.get('Registry'), 'Unexpected file component keypath/GUID')
        require(component['Condition'] == '' and int(component['Attributes']) == 260, 'Unexpected file component attributes/condition')
        size = (Path(plan['payload']) / name).stat().st_size
        require(int(row['FileSize']) == size and int(row['Attributes']) & 16384 and not int(row['Attributes']) & (4096 | 8192), 'MSI files must be compressed with exact sizes')
        file_map[row['File']] = name
        sizes[row['File']] = size
    require(set(file_map.values()) == set(plan['files']), 'MSI file manifest differs from staged payload')
    for component, root, name, value in (('InstallRegistry', '2', 'Path', '[APPDIR]'), ('DesktopShortcutComponent', '1', 'DesktopShortcut', '#1'), ('ManagedDirectories', '1', 'InstallerDirectories', '#1')):
        row = by_component.get(component, {})
        require((row.get('Root'), row.get('Key'), row.get('Name'), row.get('Value')) == (root, identity.registry_key, name, value), 'Unexpected registry target/value')
        require(components[component]['Directory_'] == ('TARGETDIR' if component == 'InstallRegistry' else 'APPDIR') and components[component]['KeyPath'] == row.get('Registry') and int(components[component]['Attributes']) == 260, 'Registry components require an HKLM/HKCU x64 registry keypath')
    require(components['InstallRegistry']['Condition'] == '', 'Install path registry must be unconditional')
    require(components['ManagedDirectories']['Condition'] == '', 'Empty-directory removal must be unconditional')
    for component, name in (('InstallRegistry', 'registry:path'), ('ManagedDirectories', 'directories:empty'), ('DesktopShortcutComponent', 'shortcut:desktop')):
        require(components[component]['ComponentId'] == authoring._guid(identity, name), 'Unexpected registry/directory/shortcut component GUID')
    managed_directories = {'APPDIR'} | {row['Directory_'] for row in components.values() if row['Component'].startswith('Cmp')}
    for directory in list(managed_directories):
        current = directory
        while current != 'APPDIR':
            relative_directory(current)
            managed_directories.add(current)
            current = directories[current]['Directory_Parent']
    removals = tables.get('RemoveFile', [])
    require(len(removals) == len(managed_directories) and {row['DirProperty'] for row in removals} == managed_directories, 'Unexpected empty-directory removal set')
    require(all(row['FileName'] == '' and row['InstallMode'] == '2' and row['Component_'] == 'ManagedDirectories' for row in removals), 'Wildcard/file removal is forbidden; only empty application directories may be removed')
    require(components['DesktopShortcutComponent']['Condition'] == 'CREATE_SHORTCUTS = "1" OR ((NOT CREATE_SHORTCUTS) AND (PREVIOUSDESKTOP OR PREVIOUSDESKTOPLINK))', 'Shortcuts must use the bounded opt-out condition')
    shortcuts = tables.get('Shortcut', [])
    require(len(shortcuts) == 2 and {row['Shortcut'] for row in shortcuts} == {'DesktopShortcut', 'StartMenuShortcut'}, 'Unexpected shortcuts')
    for row in shortcuts:
        require(row['Component_'] == 'DesktopShortcutComponent' and row['Directory_'] == ('DesktopFolder' if row['Shortcut'] == 'DesktopShortcut' else 'ProgramMenuFolder'), 'Unexpected shortcut destination')
        require(row['Name'].split('|')[-1] == identity.shortcut_name and row['Target'] == '[APPDIR]Resources\\Bin\\XXMI Launcher.exe' and row['WkDir'] == 'APPDIR' and row['Arguments'] == '', 'Unexpected shortcut command')
    allowed_actions = {
        'SetAPPDIR': ('51', 'APPDIR', '[EXISTINGAPPDIR]'),
        'WixUIPrintEula': ('65', 'WixUIWixca', 'PrintEula'),
        'WixUIValidatePath': ('65', 'WixUIWixca', 'ValidatePath'),
    }
    for row in tables.get('CustomAction', []):
        require(row['Action'] in allowed_actions and (row['Type'], row['Source'], row['Target']) == allowed_actions[row['Action']], 'Executable/unrecognized MSI custom action')
    require('SetAPPDIR' in {row['Action'] for row in tables.get('CustomAction', [])}, 'Missing existing-path property action')
    for table in ('InstallExecuteSequence', 'InstallUISequence'):
        actions = {row['Action']: row for row in tables.get(table, [])}
        require(actions.get('SetAPPDIR', {}).get('Condition') == 'NOT APPDIR AND EXISTINGAPPDIR', 'Existing-path action must not override explicit APPDIR')
        require(int(actions['SetAPPDIR']['Sequence']) > int(actions['AppSearch']['Sequence']), 'APPDIR lookup must follow AppSearch')
        if table == 'InstallExecuteSequence':
            require(not {'WixUIPrintEula', 'WixUIValidatePath'} & set(actions), 'UI DLL must not run in the execution sequence')
            require(int(actions['InstallInitialize']['Sequence']) < int(actions['RemoveExistingProducts']['Sequence']) < int(actions['ProcessComponents']['Sequence']), 'Major upgrade must remove the old product within the transaction')
    validate_ui_binaries(tables.get('Binary', []))
    require(tables.get('Upgrade') and all(row['UpgradeCode'] == identity.upgrade_code for row in tables['Upgrade']), 'Unexpected upgrade family')
    searches = tables.get('RegLocator', [])
    require(len(searches) == 2 and {(row['Root'], row['Key'], row['Name'], row['Type']) for row in searches} == {('2', identity.registry_key, 'Path', '18'), ('1', identity.registry_key, 'DesktopShortcut', '18')}, 'Unexpected registry search')
    require(all(row['Path'] in ('', '[DesktopFolder]') for row in tables.get('DrLocator', [])), 'Unexpected directory search')
    require(all(row['FileName'].split('|')[-1] == identity.shortcut_name + '.lnk' for row in tables.get('Signature', [])), 'Unexpected file search')
    return properties, file_map, sizes


def validate_msi(msi, payload, version, identity=None):
    identity = identity or authoring.PRODUCTION_IDENTITY
    plan = authoring.validate_payload(Path(payload), version)
    msi = Path(msi).resolve(strict=True)
    database = ReadOnlyDatabase(msi)
    try:
        names = [row['Name'] for row in database.rows('SELECT `Name` FROM `_Tables`')]
        tables = {}
        for name in names:
            require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name), 'Invalid MSI table name')
            # Binary data is never interpreted as text or loaded as a DLL.
            tables[name] = database.rows('SELECT `Name` FROM `Binary`') if name == 'Binary' else database.rows(f'SELECT * FROM `{name}`')
        properties, files, sizes = validate_tables(tables, database.template(), plan, identity)
        hashes = {}
        media = tables.get('Media', [])
        require(len(media) == 1 and media[0]['DiskId'] == '1' and media[0]['Cabinet'] == '#launcher.cab' and media[0]['Source'] == '', 'Exactly one embedded launcher.cab is required')
        limit = sum(sizes.values()) + len(sizes) * 1024 + 1024 * 1024
        for row in media:
            cabinet = database.stream(row['Cabinet'][1:], limit)
            for identifier, sha256 in cabinet_hashes(cabinet, sizes).items():
                require(identifier not in hashes, 'Duplicate file across embedded cabinets')
                hashes[identifier] = sha256
        require({files[key]: value for key, value in hashes.items()} == plan['files'], 'Embedded cabinet SHA-256 differs from the staged payload')
    finally:
        database.close()
    return {'format': 1, 'msi': str(msi), 'msi_sha256': authoring.digest(msi), 'version': version,
            'product_code': properties['ProductCode'], 'identity': asdict(identity),
            'payload_sha256': plan['payload_sha256'], 'launcher_source': plan['launcher_source'],
            'files': plan['files'], 'read_only': True,
            'qualification': 'Static database and embedded-payload validation only; no installation or application execution.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--msi', required=True, type=Path)
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = validate_msi(args.msi, args.payload, args.version)
    args.output.write_text(json.dumps(result, indent=4) + '\n', encoding='utf-8')
    print('PASS: MSI database, identity, installation actions, and embedded payload match')


if __name__ == '__main__':
    main()
