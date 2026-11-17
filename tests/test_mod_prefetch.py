import os
import tempfile
import threading
import unittest
from pathlib import Path

from core import mod_prefetch


class ModPrefetchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.importer = Path(temp.name) / 'ZZMI'
        self.write('d3dx.ini', '[Loader]\ntarget = Game.exe\n[Include]\n; comment\ninclude = Core\\lib.ini\n'
                               'include_recursive = Mods\nexclude_recursive = DISABLED*\n[System]\n'
                               'include_recursive = Ignored\n')
        self.write('Core/lib.ini', '[CommandListLib]\n')
        self.write('Mods/A Skin/mod.ini', '[ResourceA]\nfilename = Textures\\a.dds\n[ResourceB]\n'
                                          'FileName = "b.buf"\n; filename = commented.buf\n'
                                          'filename_extra = no.buf\n[ResourceMissing]\nfilename = gone.buf\n')
        self.write('Mods/A Skin/Textures/a.dds', b'a' * 3000)
        self.write('Mods/A Skin/b.buf', b'b' * 2000)
        self.write('Mods/A Skin/DISABLED_old.ini', '[ResourceOld]\nfilename = old.buf\n')
        self.write('Mods/DISABLED Other/mod.ini', '[ResourceX]\nfilename = x.buf\n')
        self.write('Mods/DISABLED Other/x.buf', b'x')
        self.write('Mods/B Skin/mod.ini', '[ResourceShared]\nfilename = ..\\A Skin\\b.buf\n')

    def write(self, relative, data):
        path = self.importer / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def relative(self, paths):
        return [os.path.relpath(path, self.importer).replace(os.sep, '/') for path in paths]

    def test_include_settings_only_from_include_section(self):
        settings = mod_prefetch.read_include_settings(self.importer / 'd3dx.ini')
        self.assertEqual(settings.files, [self.importer / 'Core' / 'lib.ini'])
        self.assertEqual(settings.recursive_folders, [self.importer / 'Mods'])
        self.assertEqual(settings.exclude_patterns, ['disabled*'])

    def test_collects_loaded_inis_and_their_resources_in_load_order(self):
        files = self.relative(mod_prefetch.collect_mod_files(self.importer / 'd3dx.ini'))
        self.assertEqual(files[0], 'Core/lib.ini')
        self.assertEqual(files[1:], ['Mods/A Skin/mod.ini', 'Mods/A Skin/Textures/a.dds', 'Mods/A Skin/b.buf',
                                     'Mods/A Skin/gone.buf', 'Mods/B Skin/mod.ini'])

    def test_prefetch_reads_within_budget_and_skips_missing(self):
        files = mod_prefetch.collect_mod_files(self.importer / 'd3dx.ini')
        stats = mod_prefetch.prefetch_files(files, budget=10 ** 9)
        self.assertTrue(stats.completed)
        self.assertEqual(stats.bytes, sum(os.path.getsize(f) for f in files if os.path.exists(f)))
        self.assertGreaterEqual(stats.skipped_files, 1)
        limited = mod_prefetch.prefetch_files(files, budget=2500)
        self.assertLessEqual(limited.bytes, 2500)

    def test_stop_event_ends_prefetch(self):
        stop = threading.Event()
        stop.set()
        stats = mod_prefetch.prefetch_files(mod_prefetch.collect_mod_files(self.importer / 'd3dx.ini'), 10 ** 9, stop)
        self.assertEqual(stats.files, 0)
        self.assertFalse(stats.completed)

    def test_background_prefetcher_replaces_previous_run(self):
        first = mod_prefetch.ModPrefetcher.start(self.importer / 'd3dx.ini', budget=10 ** 9)
        second = mod_prefetch.ModPrefetcher.start(self.importer / 'd3dx.ini', budget=10 ** 9)
        self.assertTrue(first.stop_event.is_set())
        second.thread.join(10)
        self.assertTrue(second.stats.completed)
        self.assertGreater(second.stats.bytes, 0)


if __name__ == '__main__':
    unittest.main()
