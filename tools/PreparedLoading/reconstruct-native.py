"""Reconstruct the pinned first-visit native source in a new local checkout.

No compiler, game, installer, deployment or network operation is invoked. Input
bytes are verified before creating the destination. The receipt distinguishes a
verified patch chain from a match to the historical development-source digest.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from zipfile import ZipFile


CAMPAIGN = 'docs/campaigns/xxmi-first-visit-exec-20261028'
TOOLS = 'tools/PreparedLoading'
PATCHES = ('native.patch', 'native-speed.patch', 'native-loading.patch',
           'native-gpu-startup.patch', 'native-gpu-retention.patch')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
SHA1 = re.compile(r'[0-9a-f]{40}\Z')


class ReconstructionError(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReconstructionError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def safe_relative(name):
    if not isinstance(name, str) or '\\' in name or '\0' in name:
        raise ReconstructionError('invalid input path')
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in ('.', '..') for p in path.parts) or ':' in name:
        raise ReconstructionError('input path must be a relative child: ' + name)
    if path.as_posix() != name:
        raise ReconstructionError('input path is not canonical: ' + name)
    return Path(*path.parts)


def verified_inputs(launcher, manifest):
    value = load_json(manifest)
    if value.get('schema') != 'xxmi.campaign-source-inputs.v1' or not isinstance(value.get('files'), list):
        raise ReconstructionError('unsupported source input manifest')
    required = {TOOLS + '/' + p for p in PATCHES} | {
        TOOLS + '/native-candidate.json', TOOLS + '/baseline/version.generated.h',
        TOOLS + '/integration-evidence/native-history.bundle',
        'development-builds/prepared-loading/BUILD-INFO.json'}
    records = {}
    for entry in value['files']:
        name = entry.get('path')
        relative = safe_relative(name)
        if name in records:
            raise ReconstructionError('duplicate input path: ' + name)
        path = launcher / relative
        expected_size, expected_hash = entry.get('bytes'), entry.get('sha256')
        if type(expected_size) is not int or expected_size < 0 or not isinstance(expected_hash, str) or not SHA256.fullmatch(expected_hash):
            raise ReconstructionError('invalid input identity: ' + name)
        readable = not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(launcher)
        if not readable and name in required:
            raise ReconstructionError('input is missing or escapes launcher: ' + name)
        data = path.read_bytes() if readable else None
        current_hash = digest(data) if data is not None else None
        current_size = len(data) if data is not None else None
        matches = current_size == expected_size and current_hash == expected_hash
        if not matches and name in required:
            raise ReconstructionError('input identity mismatch: ' + name)
        records[name] = {'path': name, 'sha256': expected_hash, 'bytes': expected_size,
                         'role': 'native-source-input' if name in required else 'helper-provenance',
                         'current_sha256': current_hash, 'current_bytes': current_size,
                         'matches_pin': matches}
    if not required.issubset(records):
        raise ReconstructionError('missing required pinned inputs: ' + ', '.join(sorted(required - records.keys())))
    return records


def git(cwd, *args, input_bytes=None):
    # A task-owned empty hooks directory prevents local/global Git hooks from
    # turning source reconstruction into an unrelated executable action.
    with tempfile.TemporaryDirectory(prefix='xxmi-git-hooks-') as hooks:
        command = ['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf',
                   '-c', 'core.hooksPath=' + hooks, '-C', str(cwd), *map(str, args)]
        result = subprocess.run(command, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise ReconstructionError('git ' + args[0] + ' failed: ' + detail)
    return result.stdout


def patch_view(data):
    """Only normalize CRLF line terminators; never trim context or alter input."""
    view = data.replace(b'\r\n', b'\n')
    return view, ('crlf-to-lf' if view != data else 'identity')


def apply_patch(destination, patch):
    view, transform = patch_view(patch.read_bytes())
    git(destination, 'apply', '--check', '--index', '-', input_bytes=view)
    git(destination, 'apply', '--index', '-', input_bytes=view)
    return {'path': patch.name, 'application_transform': transform,
            'application_sha256': digest(view)}


def bundle_parent(destination, bundle, candidate):
    git(destination, 'bundle', 'verify', bundle)
    heads = git(destination, 'bundle', 'list-heads', bundle).decode('utf-8').splitlines()
    refs = [line.split(' ', 1)[1] for line in heads if line.split(' ', 1)[0] == candidate['native_parent']]
    if not refs:
        raise ReconstructionError('bundle does not advertise the pinned native parent')
    git(destination, 'fetch', '--no-tags', bundle, refs[0])
    if git(destination, 'rev-parse', 'FETCH_HEAD').decode().strip() != candidate['native_parent']:
        raise ReconstructionError('bundle native parent mismatch')
    tree = git(destination, 'rev-parse', candidate['native_parent'] + '^{tree}').decode().strip()
    if tree != candidate['native_tree']:
        raise ReconstructionError('bundle native tree mismatch')
    return tree


def source_manifest(destination):
    paths = git(destination, 'ls-files', '-z', '--cached').split(b'\0')
    names = {name.decode('utf-8') for name in paths if name}
    names.add('version.generated.h')
    files = []
    combined = hashlib.sha256()
    for name in sorted(names):
        relative = safe_relative(name)
        path = destination / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(destination):
            raise ReconstructionError('unsupported native source entry: ' + name)
        data = path.read_bytes()
        file_hash = digest(data)
        files.append({'path': name, 'bytes': len(data), 'sha256': file_hash})
        combined.update((name + '\0' + file_hash + '\n').encode('utf-8'))
    return files, combined.hexdigest()


def historical_archive_scope(launcher, destination, expected):
    """Compare the exact historical ZIP name scope without executing its payload.

    Old packaging may omit files now present in the reconstructed tree. Keep
    this scoped comparison separate from the complete source manifest.
    """
    relative = 'development-builds/prepared-loading/XXMI-Prepared-Loading-development-win-x64.zip'
    archive = launcher / relative
    if not archive.is_file():
        return {'available': False}
    if archive.is_symlink() or not archive.resolve().is_relative_to(launcher) or archive.stat().st_size > 64 * 1024 * 1024:
        raise ReconstructionError('unsupported historical development archive')
    with ZipFile(archive) as outer:
        entries = [entry for entry in outer.infolist() if entry.filename == 'corresponding-source.zip']
        if len(entries) != 1 or entries[0].file_size > 64 * 1024 * 1024:
            raise ReconstructionError('historical corresponding-source archive is missing, duplicated or oversized')
        inner_bytes = outer.read(entries[0])
    with ZipFile(io.BytesIO(inner_bytes)) as inner:
        names = [entry.filename for entry in inner.infolist() if not entry.is_dir()]
    if not names or len(names) > 10000 or len(names) != len(set(names)):
        raise ReconstructionError('invalid historical source archive name list')
    with_prefix, relative_names = hashlib.sha256(), hashlib.sha256()
    missing = []
    for name in sorted(names):
        if not name.startswith('native/'):
            raise ReconstructionError('unexpected historical source archive path: ' + name)
        relative_name = name[len('native/'):]
        path = destination / safe_relative(relative_name)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(destination):
            missing.append(relative_name)
            continue
        file_hash = digest(path.read_bytes())
        with_prefix.update((name + '\0' + file_hash + '\n').encode('utf-8'))
        relative_names.update((relative_name + '\0' + file_hash + '\n').encode('utf-8'))
    return {'available': True, 'path': relative, 'archive_sha256': digest(archive.read_bytes()),
            'corresponding_source_sha256': digest(inner_bytes), 'files': len(names),
            'missing': missing, 'zip_name_scope_digest': with_prefix.hexdigest(),
            'relative_name_scope_digest': relative_names.hexdigest(),
            'zip_name_scope_matches_record': not missing and with_prefix.hexdigest() == expected,
            'relative_name_scope_matches_record': not missing and relative_names.hexdigest() == expected}


def reconstruct(launcher, native, output, inputs, route='aggregate', verify_bundle=False):
    launcher, native, output, inputs = (p.resolve() for p in (launcher, native, output, inputs))
    if output.exists():
        raise ReconstructionError('destination must not exist: ' + str(output))
    if output.is_relative_to(native) or output.is_relative_to(launcher):
        raise ReconstructionError('destination must be outside both input repositories')
    records = verified_inputs(launcher, inputs)
    candidate = load_json(launcher / TOOLS / 'native-candidate.json')
    for name in ('native_base', 'native_parent', 'native_tree'):
        if not isinstance(candidate.get(name), str) or not SHA1.fullmatch(candidate[name]):
            raise ReconstructionError('invalid pinned Git identity: ' + name)
    if candidate.get('patch_sha256') != records[TOOLS + '/native.patch']['sha256']:
        raise ReconstructionError('aggregate patch is not bound to native candidate')
    if git(native, 'rev-parse', candidate['native_base'] + '^{commit}').decode().strip() != candidate['native_base']:
        raise ReconstructionError('pinned public base is not available locally')
    historical = load_json(launcher / 'development-builds/prepared-loading/BUILD-INFO.json')
    expected = historical.get('native_source_digest')
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise ReconstructionError('historical native source digest is missing')

    output.parent.mkdir(parents=True, exist_ok=True)
    git(output.parent, 'clone', '--local', '--no-hardlinks', '--no-checkout', '--no-recurse-submodules', native, output)
    git(output, 'checkout', '--detach', candidate['native_base'])
    steps = []
    bundle = launcher / TOOLS / 'integration-evidence/native-history.bundle'
    if route == 'bundle' or verify_bundle:
        bundle_parent(output, bundle, candidate)
    if route == 'bundle':
        git(output, 'checkout', '--detach', candidate['native_parent'])
    elif route == 'aggregate':
        steps.append(apply_patch(output, launcher / TOOLS / PATCHES[0]))
    else:
        raise ReconstructionError('unsupported source route: ' + route)
    actual_tree = git(output, 'write-tree').decode().strip()
    if actual_tree != candidate['native_tree']:
        raise ReconstructionError('aggregate source differs from pinned parent tree')
    for name in PATCHES[1:]:
        steps.append(apply_patch(output, launcher / TOOLS / name))
    final_tree = git(output, 'write-tree').decode().strip()
    shutil.copyfile(launcher / TOOLS / 'baseline/version.generated.h', output / 'version.generated.h')
    files, source_digest = source_manifest(output)
    archive_scope = historical_archive_scope(launcher, output, expected)
    return {
        'schema': 'xxmi.native-reconstruction.v1', 'route': route,
        'base': candidate['native_base'], 'parent': candidate['native_parent'],
        'parent_tree': actual_tree, 'bundle_verified': route == 'bundle' or verify_bundle,
        'reconstructed_git_tree': final_tree,
        'inputs_manifest_sha256': digest(inputs.read_bytes()),
        'inputs': list(records.values()), 'patch_application': steps,
        'source_digest_algorithm': 'sha256(sorted(path + NUL + file_sha256 + LF), UTF-8); version header included',
        'source_digest': source_digest, 'historical_source_digest': expected,
        'historical_comparison_algorithm': 'sha256(sorted(archive_member_name + NUL + reconstructed_file_sha256 + LF), UTF-8); original corresponding-source.zip names including native/',
        'historical_source_matches': archive_scope['zip_name_scope_matches_record'] if archive_scope['available'] else None,
        'historical_archive_scope': archive_scope,
        'files': files,
        'qualification': 'Source reconstruction only; historical matching covers the archived member-name scope, with additional source files separately manifested. No compile, native execution, deployment or game qualification.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--native', type=Path, required=True, help='Local Git repository containing the pinned public base')
    parser.add_argument('--output', type=Path, required=True, help='New task-owned destination outside the input repositories')
    parser.add_argument('--inputs', type=Path, help='Pinned input manifest; defaults to campaign/source-inputs.json')
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--route', choices=('aggregate', 'bundle'), default='aggregate')
    parser.add_argument('--verify-bundle', action='store_true')
    args = parser.parse_args()
    inputs = args.inputs or args.launcher / CAMPAIGN / 'source-inputs.json'
    try:
        receipt_path = args.receipt.resolve()
        if receipt_path.exists() or receipt_path.is_relative_to(args.launcher.resolve()) or receipt_path.is_relative_to(args.native.resolve()):
            raise ReconstructionError('receipt must be a new file outside the input repositories')
        receipt = reconstruct(args.launcher, args.native, args.output, inputs, args.route, args.verify_bundle)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({key: receipt[key] for key in ('reconstructed_git_tree', 'source_digest', 'historical_source_matches')}))
        if not receipt['historical_source_matches']:
            parser.exit(1, 'Source reconstructed, but historical byte-for-byte equivalence is NOT PROVEN; inspect the receipt.\n')
    except (ReconstructionError, OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
