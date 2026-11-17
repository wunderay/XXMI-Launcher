import fnmatch
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import core.config_manager  # noqa: F401  (Windows: real module)
    mod_manager = __import__('core.mod_manager', fromlist=['mod_manager'])
    _STUBBED = False
except ImportError:
    # Linux: the config manager needs Windows packages; mod_manager reads only two fields.
    _saved = sys.modules.get('core.config_manager')
    sys.modules['core.config_manager'] = SimpleNamespace(
        Launcher=SimpleNamespace(active_importer='ZZMI'), Active=SimpleNamespace(Importer=None))
    try:
        from core import mod_manager
    finally:
        if _saved is None:
            sys.modules.pop('core.config_manager', None)
        else:
            sys.modules['core.config_manager'] = _saved
    _STUBBED = True

import core.event_manager as Events


def reference_listing(folder: Path, patterns):
    """The previous implementation: os.walk(followlinks=True) + should_exclude on every part."""
    paths = [Path(root) / name for root, dirs, files in os.walk(folder, followlinks=True)
             for name in files if name.endswith('.ini')]
    return [p for p in paths if not any(fnmatch.fnmatch(part.lower(), pat.lower())
                                        for part in p.relative_to(folder).parts for pat in patterns)]


class ModsFolderTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.mods = self.base / 'ZZMI' / 'Mods'
        self.write('Hero Skin/mod.ini', '[TextureOverrideA]\nhash = 1\n')
        self.write('Hero Skin/Variants/alt.ini', '[TextureOverrideB]\n')
        self.write('Hero Skin/Variants/DISABLED_old.ini', '[TextureOverrideC]\n')
        self.write('Hero Skin/texture.dds', 'not an ini')
        self.write('DISABLED Villain/mod.ini', '[TextureOverrideD]\n')
        self.write('DISABLED Villain/Deep/More/mod.ini', '[TextureOverrideE]\n')
        self.write('Pack/Inner/disabled_lower/x.ini', '[TextureOverrideF]\n')
        self.write('Pack/Inner/keep.ini', 'namespace = global\\Pack\n')
        self.write('standalone.ini', '[TextureOverrideG]\n')
        self.patterns = ['DISABLED*']

    def write(self, relative, text):
        path = self.mods / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path


class ScanIniFilesTests(ModsFolderTestCase):
    def assert_matches_reference(self):
        scanned = mod_manager.IniValidator.scan_ini_files(self.mods, self.patterns)
        expected = reference_listing(self.mods, self.patterns)
        self.assertEqual([item.path for item in scanned], expected)
        for item in scanned:
            self.assertEqual(item.key, str(item.path.resolve()))
            self.assertEqual(item.mtime, item.path.stat().st_mtime)

    def test_listing_keys_and_times_match_previous_implementation(self):
        self.assert_matches_reference()

    def test_excluded_folders_are_not_listed(self):
        listed = []
        original = os.scandir

        def recording_scandir(path):
            listed.append(Path(path).name)
            return original(path)

        with mock.patch.object(mod_manager.os, 'scandir', recording_scandir):
            mod_manager.IniValidator.scan_ini_files(self.mods, self.patterns)
        self.assertNotIn('DISABLED Villain', listed)
        self.assertNotIn('Deep', listed)
        self.assertNotIn('disabled_lower', listed)
        self.assertIn('Variants', listed)

    def test_symlinked_mod_folder_and_file_use_resolved_keys(self):
        outside = self.base / 'Library' / 'Linked'
        outside.mkdir(parents=True)
        (outside / 'linked.ini').write_text('[TextureOverrideL]\n', encoding='utf-8')
        try:
            (self.mods / 'LinkedMod').symlink_to(outside, target_is_directory=True)
            (self.mods / 'filelink.ini').symlink_to(outside / 'linked.ini')
        except (OSError, NotImplementedError):
            self.skipTest('Symlinks are not available')
        self.assert_matches_reference()
        keys = {item.key for item in mod_manager.IniValidator.scan_ini_files(self.mods, self.patterns)}
        self.assertIn(str((outside / 'linked.ini').resolve()), keys)

    def test_link_back_to_an_ancestor_does_not_loop(self):
        try:
            (self.mods / 'Pack' / 'Inner' / 'loop').symlink_to(self.mods, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlinks are not available')
        scanned = mod_manager.IniValidator.scan_ini_files(self.mods, self.patterns)
        self.assertEqual(len({item.key for item in scanned}), len(scanned))
        self.assertEqual(len(scanned), 4)


class ReadersTests(unittest.TestCase):
    CASES = [
        b'namespace = a\\b\n',
        b'\r\n; c\r\n\r\n  Namespace=x\r\n[S]',
        b'; only\n;comment',
        b'',
        b';c\rnamespace = mac\r',
        b'\xef\xbb\xbfnamespace = bom\n',
        b';' + b'c' * 40000 + b'\n   namespace = far\n',
        b'\n\n\n',
        'namé = 1'.encode(),
    ]

    def test_readers_match_text_mode_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            for index, data in enumerate(self.CASES):
                path = Path(temp) / f'{index}.ini'
                path.write_bytes(data)
                self.assertEqual(mod_manager.read_ini_text(path).splitlines(),
                                 path.read_text(encoding='utf-8').splitlines())
                expected = None
                with path.open('r', encoding='utf-8') as lines:
                    for line in lines:
                        if line.lstrip() and not line.lstrip().startswith(';'):
                            expected = line.lstrip().rstrip('\n')
                            break
                for chunk_size in (3, 16384):
                    got = mod_manager.read_first_ini_line(path, chunk_size=chunk_size)
                    self.assertEqual(got.rstrip('\r\n') if got is not None else None, expected, (index, chunk_size))


class OptimizerCacheTests(ModsFolderTestCase):
    def setUp(self):
        super().setUp()
        self.cache = self.base / 'Cache' / 'ZZMI.json'
        config = SimpleNamespace(Launcher=SimpleNamespace(active_importer='ZZMI'),
                                 Active=SimpleNamespace(Importer=SimpleNamespace(importer_path=self.mods.parent)))
        patcher = mock.patch.object(mod_manager, 'Config', config)
        patcher.start()
        self.addCleanup(patcher.stop)
        if os.name != 'nt':
            # Overwriting files goes through a Windows-only read-only attribute check
            writer = mock.patch.object(mod_manager.Paths.App, 'write_file',
                                       lambda path, data, **kwargs: Path(path).write_text(data, encoding='utf-8'))
            writer.start()
            self.addCleanup(writer.stop)
        events = mock.patch.object(Events, 'Call', lambda *args, **kwargs: False)
        events.start()
        self.addCleanup(events.stop)

    def optimize(self, reset_cache=False):
        validated = []
        original = mod_manager.IniValidator.validate_ini

        def counting(validator, lines):
            validated.append(lines)
            return original(validator, lines)

        with mock.patch.object(mod_manager.IniValidator, 'validate_ini', counting):
            mod_manager.ModManager().optimize_mods_folder(
                self.mods, cache_path=self.cache, dry_run=True, use_cache=True,
                reset_cache=reset_cache, exclude_patterns=self.patterns)
        return len(validated)

    def test_unchanged_files_are_skipped_on_the_next_launch(self):
        self.assertEqual(self.optimize(), len(reference_listing(self.mods, self.patterns)))
        self.assertEqual(self.optimize(), 0)
        path = self.mods / 'Hero Skin' / 'mod.ini'
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        self.assertEqual(self.optimize(), 1)

    def test_reset_cache_is_saved(self):
        self.optimize()
        self.cache.unlink()
        self.assertEqual(self.optimize(reset_cache=True), len(reference_listing(self.mods, self.patterns)))
        self.assertTrue(self.cache.exists())
        self.assertEqual(self.optimize(), 0)

    def test_disabled_duplicate_library_is_not_validated(self):
        library = self.mods.parent / 'Core' / 'ZZMI' / 'Libraries' / 'lib.ini'
        library.parent.mkdir(parents=True)
        library.write_text('namespace = global\\Pack\n', encoding='utf-8')
        validated_paths = []
        original = mod_manager.IniValidator.validate_folder

        def recording(validator, ini_files=None):
            validated_paths.extend(ini_file.path for ini_file in ini_files or [])
            return original(validator, ini_files)

        with mock.patch.object(mod_manager.ModManager, 'show_duplicate_libraries_notification', lambda *a: True), \
                mock.patch.object(mod_manager.IniValidator, 'validate_folder', recording):
            mod_manager.ModManager().optimize_mods_folder(
                self.mods, cache_path=self.cache, dry_run=False, use_cache=True, exclude_patterns=self.patterns)
        duplicate = self.mods / 'Pack' / 'Inner' / 'keep.ini'
        self.assertFalse(duplicate.exists())
        self.assertTrue((self.mods / 'Pack' / 'Inner' / 'DISABLED_keep.ini').exists())
        # The listing taken before disabling is replaced: the renamed library is not validated
        self.assertNotIn(duplicate, validated_paths)
        self.assertEqual(sorted(validated_paths), sorted(reference_listing(self.mods, self.patterns)))


if __name__ == '__main__':
    unittest.main()
