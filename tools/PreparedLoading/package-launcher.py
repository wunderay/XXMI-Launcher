'''Stage and smoke-test the standard launcher installation payload for release tooling.'''
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def validate_version(value: str) -> str:
    if not re.fullmatch(r'(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})', value):
        raise argparse.ArgumentTypeError('Version must have three numeric components, for example 1.2.3')
    if any(int(part) > 65535 for part in value.split('.')):
        raise argparse.ArgumentTypeError('Version components must be between 0 and 65535')
    return value


def version_resource(version: str) -> str:
    version = validate_version(version)
    numeric = tuple(int(part) for part in version.split('.')) + (0,)
    display = version + '.0'
    description = 'XXMI Launcher development build' if version == '0.0.0' else 'XXMI Launcher'
    product = 'XXMI Launcher development' if version == '0.0.0' else 'XXMI Launcher'
    flags = '0x2' if version == '0.0.0' else '0x0'
    return f'''VSVersionInfo(
    ffi=FixedFileInfo(
        filevers={numeric}, prodvers={numeric},
        mask=0x3f, flags={flags}, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'XXMI Launcher contributors'),
        StringStruct('FileDescription', {description!r}),
        StringStruct('FileVersion', {display!r}),
        StringStruct('InternalName', 'XXMI Launcher'),
        StringStruct('OriginalFilename', 'XXMI Launcher.exe'),
        StringStruct('ProductName', {product!r}),
        StringStruct('ProductVersion', {display!r})])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
'''


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def file_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): digest(path)
            for path in sorted(root.rglob('*')) if path.is_file()}


def source_status(root: Path) -> str:
    # Build output and temporary files are outside these input paths. Include
    # untracked inputs so an extra module or asset cannot silently enter a build.
    return subprocess.check_output([
        'git', '-C', str(root), 'status', '--porcelain', '--untracked-files=all',
        '--', 'src', 'Themes', 'Locale', 'requirements.txt', 'LICENSE', 'tools/PreparedLoading',
        'tests/test_launcher_distribution.py', 'tests/test_launcher_user_flow.py',
        'tools/Installer', 'tests/test_installer_authoring.py',
        'tests/test_installer_update.py', 'tests/test_installer_windows.py',
        '.github/workflows/launcher-installer.yml',
        '.github/workflows/prepared-loading-checks.yml',
    ], text=True).strip()


# Windows refuses to start an executable whose manifest requires administrator rights from a
# process that is not elevated (ERROR_ELEVATION_REQUIRED)
ELEVATION_REQUIRED = 740
REQUIRED_EXECUTION_LEVEL = 'requireAdministrator'


def is_elevated() -> bool:
    """True when this process may start a requireAdministrator executable (always outside Windows)."""
    if os.name != 'nt':
        return True
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def execution_level_from_manifest(manifest: bytes) -> str | None:
    """The `level` of the manifest's requestedExecutionLevel, or None without one."""
    match = re.search(rb'<(?:\w+:)?requestedExecutionLevel\b[^>]*\blevel\s*=\s*["\']([A-Za-z]+)["\']', manifest)
    return match.group(1).decode('ascii') if match else None


def requested_execution_level(executable: Path) -> str | None:
    """The UAC execution level the executable's embedded manifest requests (None without one)."""
    import pefile  # Build environment only; the version-resource step runs without it
    pe = pefile.PE(str(executable), fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        resources = getattr(pe, 'DIRECTORY_ENTRY_RESOURCE', None)
        for resource_type in resources.entries if resources else []:
            if resource_type.id != pefile.RESOURCE_TYPE['RT_MANIFEST']:
                continue
            for name in resource_type.directory.entries:
                for language in name.directory.entries:
                    data = language.data.struct
                    level = execution_level_from_manifest(pe.get_data(data.OffsetToData, data.Size))
                    if level:
                        return level
        return None
    finally:
        pe.close()


def stage_payload(root: Path, output: Path) -> Path:
    frozen = output / 'build/dist/XXMI Launcher'
    if not (frozen / 'XXMI Launcher.exe').is_file():
        raise RuntimeError('Missing frozen Windows executable')
    bundle = output / 'install-payload'
    # A clean stage prevents obsolete modules/assets surviving a repeated build.
    if bundle.exists():
        shutil.rmtree(bundle)
    shutil.copytree(frozen, bundle / 'Resources/Bin')
    for directory in ('Themes', 'Locale'):
        shutil.copytree(root / directory, bundle / directory,
                        ignore=shutil.ignore_patterns('active_locale.cfg', '__pycache__'))
    shutil.copy2(root / 'LICENSE', bundle / 'LICENSE')
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--version', type=validate_version, default='0.0.0')
    parser.add_argument('--version-resource', type=Path, help='Write PE version resource only')
    parser.add_argument('--require-elevation', action='store_true',
                        help='Stop at once unless elevated (the build self-tests an exe that requires it)')
    args = parser.parse_args()
    if args.require_elevation and not is_elevated():
        parser.exit(1, 'XXMI Launcher.exe requires administrator rights, so its build self-test does too: '
                       'run the build from an elevated shell.\n')
    if args.version_resource is not None:
        if args.root is not None or args.output is not None:
            parser.error('--version-resource cannot be combined with --root or --output')
        args.version_resource.write_text(version_resource(args.version), encoding='utf-8')
        return
    if args.root is None or args.output is None:
        parser.error('--root and --output are required when staging a payload')
    root, output = args.root.resolve(), args.output.resolve()
    (output / 'LAUNCHER-PACKAGING.json').unlink(missing_ok=True)
    (output / 'LAUNCHER-SELF-TEST.json').unlink(missing_ok=True)
    bundle = stage_payload(root, output)
    # The launcher must run elevated to inject into the game; never ship a build that does not ask
    execution_level = requested_execution_level(bundle / 'Resources/Bin/XXMI Launcher.exe')
    if execution_level != REQUIRED_EXECUTION_LEVEL:
        raise RuntimeError(f'XXMI Launcher.exe requests execution level {execution_level!r}, '
                           f'not {REQUIRED_EXECUTION_LEVEL!r}')
    status = source_status(root)
    manifest = {
        'format': 1,
        'version': args.version,
        'packager': 'PyInstaller 6.20.0; hooks-contrib 2026.5',
        'launcher_source': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'source_has_local_changes': bool(status),
        'source_input_status': status,
        'requirements_sha256': digest(root / 'requirements.txt'),
        'requested_execution_level': execution_level,
        'build_dependencies': subprocess.check_output([os.sys.executable, '-m', 'pip', 'freeze'], text=True).splitlines(),
        'qualification': 'Development packaging only; no real game compatibility or performance qualification.',
        'files': file_hashes(bundle),
    }
    (bundle / 'LAUNCHER-BUILD.json').write_text(json.dumps(manifest, indent=4) + '\n', encoding='utf-8')
    # Exercise the staged installation outside source/build paths. Only Windows
    # System32 remains on PATH to detect accidental development dependencies.
    with tempfile.TemporaryDirectory(prefix='XXMI installation smoke ') as temporary:
        smoke = Path(temporary)
        extracted = smoke / 'XXMI Launcher'
        shutil.copytree(bundle, extracted)
        before = file_hashes(extracted)
        environment = dict(os.environ)
        environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        result_path = smoke / 'launcher-self-test.json'
        environment['XXMI_LAUNCHER_SELF_TEST_REPORT'] = str(result_path)
        try:
            completed = subprocess.run([str(extracted / 'Resources/Bin/XXMI Launcher.exe'), '--self-test'],
                                       cwd=smoke, env=environment, check=False, timeout=120)
        except OSError as error:
            if getattr(error, 'winerror', None) == ELEVATION_REQUIRED:
                message = ('XXMI Launcher.exe requires administrator rights; run the build from an '
                           'elevated shell so its self-test can start')
                result = {'format': 1, 'passed': False, 'error': message}
                (output / 'LAUNCHER-SELF-TEST.json').write_text(json.dumps(result, indent=4) + '\n', encoding='utf-8')
                raise RuntimeError(message) from error
            raise
        except subprocess.TimeoutExpired as error:
            result = {'format': 1, 'passed': False, 'error': 'Frozen self-test timed out after 120 seconds'}
            (output / 'LAUNCHER-SELF-TEST.json').write_text(json.dumps(result, indent=4) + '\n', encoding='utf-8')
            raise RuntimeError(result['error']) from error
        if result_path.is_file():
            result_text = result_path.read_text(encoding='utf-8')
            (output / 'LAUNCHER-SELF-TEST.json').write_text(result_text, encoding='utf-8')
            result = json.loads(result_text)
        else:
            result = {'format': 1, 'passed': False, 'error': 'Frozen self-test exited without its result file'}
            (output / 'LAUNCHER-SELF-TEST.json').write_text(json.dumps(result, indent=4) + '\n', encoding='utf-8')
        if completed.returncode != 0 or result.get('format') != 1 or result.get('passed') is not True:
            raise RuntimeError(f'Frozen self-test failed (exit {completed.returncode}):\n{json.dumps(result, indent=4)}')
        if file_hashes(extracted) != before:
            raise RuntimeError('Frozen self-test modified the extracted bundle')
        if sorted(path.name for path in smoke.iterdir()) != ['XXMI Launcher', 'launcher-self-test.json']:
            raise RuntimeError('Frozen self-test created files outside the bundle')
    report = {
        'payload': bundle.name,
        'executable': 'Resources/Bin/XXMI Launcher.exe',
        'manifest_sha256': digest(bundle / 'LAUNCHER-BUILD.json'),
        'smoke_test': 'passed: staged installation exe; offline hidden GUI, Tcl/Tkhtml, imports, localization, image, version, file integrity; no bundle writes',
        'normal_application_started': False,
        'game_tested': False,
        'self_test_result': result,
    }
    (output / 'LAUNCHER-PACKAGING.json').write_text(json.dumps(report, indent=4) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
