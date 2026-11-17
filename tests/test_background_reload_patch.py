"""The background-reload design patch stays applicable on top of native.patch, and its paced-release
lifecycle passes a mock C++ harness (exact pump/drain/retire code, mock graph/device types; not a
D3D or DLL test). Both read the patches only; nothing under version control is modified."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools' / 'PreparedLoading'
NATIVE = TOOLS / 'native.patch'
BACKGROUND = TOOLS / 'background-reload.patch'
HARNESS = TOOLS / 'background-reload-harness' / 'lifecycle.cpp'
# Hunks for this upstream file need the full upstream tree, which is not part of this repository
UPSTREAM_ONLY = 'DirectX11/IniHandler.cpp'


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)


def new_files(patch: Path) -> list[str]:
    files, current = [], None
    for line in patch.read_bytes().decode('utf-8', errors='replace').splitlines():
        if line.startswith('diff --git '):
            current = line.split(' b/', 1)[1]
        elif line.startswith('new file mode') and current:
            files.append(current)
    return files


def between(text: str, start: str, end: str) -> str:
    """From the line starting with `start` through the end of the function starting with `end`."""
    begin = text.index(start)
    stop = text.index(end, begin)
    close = re.compile(r'^}', re.M).search(text, stop)
    return text[begin:close.end()] + '\n'


@unittest.skipIf(shutil.which('git') is None, 'git is not installed')
class BackgroundReloadPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.tree = Path(cls.temp.name) / 'tree'
        cls.tree.mkdir()
        git('init', '-q', cwd=cls.tree)
        includes = [f'--include={path}' for path in new_files(NATIVE)]
        result = git('apply', *includes, str(NATIVE), cwd=cls.tree)
        if result.returncode != 0:
            raise AssertionError(f'native.patch files could not be rebuilt: {result.stderr}')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_design_patch_applies_on_top_of_native_patch(self):
        result = git('apply', '--check', f'--exclude={UPSTREAM_ONLY}', str(BACKGROUND), cwd=self.tree)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(shutil.which('g++') is None, 'g++ is not installed')
    def test_paced_release_lifecycle_harness(self):
        with tempfile.TemporaryDirectory() as work:
            tree = Path(work) / 'tree'
            shutil.copytree(self.tree, tree)
            result = git('apply', f'--exclude={UPSTREAM_ONLY}', str(BACKGROUND), cwd=tree)
            self.assertEqual(result.returncode, 0, result.stderr)
            inl = (tree / 'DirectX11/ModLoading/BackgroundReload.inl').read_text(encoding='utf-8')
            runtime = (tree / 'DirectX11/ModLoading/Runtime.cpp').read_text(encoding='utf-8')
            # Exact slot table, pump and drain (QueueIncrementalRelease needs the real BackgroundControl)
            slots = inl[inl.index('struct IncrementalRelease {'):inl.index('void QueueIncrementalRelease')]
            # Everything after the (D3D-specific) child release helpers through DrainIncrementalRelease
            helpers_end = len(between(inl, 'void ReleaseShaderChildren', 'void ReleaseShaderChildren'))
            helpers_end += inl.index('void ReleaseShaderChildren')
            pump_and_drain = between(inl[helpers_end:], '\n', 'void DrainIncrementalRelease()')
            constant = re.search(r'^constexpr size_t IncrementalReleasePerFrame = .*$', inl, re.M).group(0)
            (Path(work) / 'exact.inc').write_text(constant + '\n' + slots + pump_and_drain, encoding='utf-8')
            (Path(work) / 'owner.inc').write_text(
                between(runtime, 'struct RuntimeOwnerScope {', 'struct RuntimeOwnerScope {') + ';\n', encoding='utf-8')
            (Path(work) / 'retire.inc').write_text(
                between(runtime, 'void RetireRuntimeDevice(', 'void RetireRuntimeDevice('), encoding='utf-8')
            binary = Path(work) / 'lifecycle'
            build = subprocess.run(['g++', '-std=c++17', '-O1', f'-I{work}', '-DEXACT="exact.inc"',
                                    '-DRETIRE="retire.inc"', '-DOWNER="owner.inc"', str(HARNESS), '-o', str(binary), '-pthread'],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            for line in ('early-return retirement: occupied=0 destroyed=4',
                         'deferred retirement, same thread: occupied=0 destroyed=4',
                         'deferred retirement, other thread: occupied=0 destroyed=4',
                         'replacement device, other thread: occupied=0 destroyed=2',
                         'reentrant drain: deferred=0 occupied=0 destroyed=2',
                         'drain while another thread holds the lock: occupied=0 destroyed=2'):
                self.assertIn(line, run.stdout)


if __name__ == '__main__':
    unittest.main()
