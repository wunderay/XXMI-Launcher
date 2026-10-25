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
        '.github/workflows/prepared-loading-checks.yml',
    ], text=True).strip()


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
    args = parser.parse_args()
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
    status = source_status(root)
    manifest = {
        'format': 1,
        'version': args.version,
        'packager': 'PyInstaller 6.20.0; hooks-contrib 2026.5',
        'launcher_source': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'source_has_local_changes': bool(status),
        'source_input_status': status,
        'requirements_sha256': digest(root / 'requirements.txt'),
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
