'''Assemble and smoke-test the exact extracted portable launcher artifact.'''
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


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
    ], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    (output / 'LAUNCHER-PACKAGING.json').unlink(missing_ok=True)
    (output / 'LAUNCHER-SELF-TEST.json').unlink(missing_ok=True)
    bundle = output / 'build/dist/XXMI Launcher'
    if not (bundle / 'XXMI Launcher.exe').is_file():
        raise RuntimeError('Missing frozen Windows executable')
    for directory in ('Themes', 'Locale'):
        shutil.copytree(root / directory, bundle / directory, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('active_locale.cfg', '__pycache__'))
    shutil.copy2(root / 'LICENSE', bundle / 'LICENSE')
    (bundle / 'START-HERE.txt').write_text(
        'Extract the complete archive into a writable folder, then double-click XXMI Launcher.exe.\n'
        'Keep Themes, Locale, and _internal beside the executable. No Python or build tools are needed.\n'
        'Development build 0.0.0: launcher self-update is disabled by the existing version check.\n'
        'This launcher does not contain or authorize a prepared-loading native DLL.\n'
        'Normal launcher startup can access the network and write its configuration.\n', encoding='utf-8')
    status = source_status(root)
    manifest = {
        'format': 1,
        'version': '0.0.0',
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
    archive = output / 'XXMI-Launcher-development-win-x64.zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                zipped.write(path, 'XXMI Launcher/' + path.relative_to(bundle).as_posix())
    # Extract and launch outside both source and build directories. Remove Python
    # and developer tools from PATH to detect accidental host dependencies.
    with tempfile.TemporaryDirectory(prefix='XXMI portable smoke ') as temporary:
        smoke = Path(temporary)
        with ZipFile(archive) as zipped:
            zipped.extractall(smoke)
        extracted = smoke / 'XXMI Launcher'
        before = file_hashes(extracted)
        environment = dict(os.environ)
        environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        result_path = smoke / 'launcher-self-test.json'
        environment['XXMI_LAUNCHER_SELF_TEST_REPORT'] = str(result_path)
        try:
            completed = subprocess.run([str(extracted / 'XXMI Launcher.exe'), '--self-test'],
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
        'archive': archive.name,
        'sha256': digest(archive),
        'smoke_test': 'passed: extracted exe; offline hidden GUI, Tcl/Tkhtml, imports, localization, image, version, file integrity; no bundle writes',
        'normal_application_started': False,
        'game_tested': False,
        'self_test_result': result,
    }
    (output / 'LAUNCHER-PACKAGING.json').write_text(json.dumps(report, indent=4) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
