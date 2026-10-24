"""Package a built development DLL with its exact corresponding source.

No installation, signing, trust configuration or game discovery is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    args = parser.parse_args()
    native, build = args.native.resolve(), args.build.resolve()
    tools = Path(__file__).resolve().parent
    info = json.loads((build / 'BUILD-INFO.json').read_text(encoding='utf-8'))
    dll = build / 'bin' / 'd3d11.dll'
    assert digest(dll) == info['dll_sha256'], 'DLL changed after build verification'
    assert digest(tools / 'native.patch') == info['patch_sha256'], 'source patch changed'

    # git apply adds new source files as untracked files. Include both tracked
    # and untracked nonignored files, while excluding build products by the
    # upstream ignore rules. Never archive .git or files outside this checkout.
    paths = subprocess.check_output([
        'git', '-C', str(native), 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
    ]).split(b'\0')
    source = build / 'corresponding-source.zip'
    source_count = 0
    with ZipFile(source, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(set(filter(None, paths))):
            relative = Path(name.decode('utf-8'))
            path = native / relative
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f'unsupported source entry: {relative}')
            path.resolve().relative_to(native)
            archive.write(path, 'native/' + relative.as_posix())
            source_count += 1
    info['corresponding_source_sha256'] = digest(source)
    info['corresponding_source_files'] = source_count
    (build / 'BUILD-INFO.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')

    package = build / 'XXMI-Prepared-Loading-development-win-x64.zip'
    entries = {
        'd3d11.dll': dll,
        'PreparationCapabilities.json': build / 'PreparationCapabilities.json',
        'BUILD-INFO.json': build / 'BUILD-INFO.json',
        'corresponding-source.zip': source,
        'native.patch': tools / 'native.patch',
        'README.md': tools / 'README.md',
        'COPYING.txt': native / 'COPYING.txt',
        'LICENSE.GPL.txt': native / 'LICENSE.GPL.txt',
    }
    with ZipFile(package, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in entries.items():
            archive.write(path, name)
    (build / 'SHA256SUMS').write_text(
        f'{digest(package)}  {package.name}\n{digest(dll)}  d3d11.dll\n', encoding='ascii')
    print(f'Packaged {package.name}; SHA256={digest(package)}; source files={source_count}')


if __name__ == '__main__':
    main()
