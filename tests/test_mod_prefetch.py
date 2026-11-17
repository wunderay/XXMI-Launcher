import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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


    def test_stopped_run_does_not_collect(self):
        stop = threading.Event()
        stop.set()
        with mock.patch.object(mod_prefetch, '_referenced_files', side_effect=AssertionError('parsed')):
            self.assertEqual(mod_prefetch.collect_mod_files(self.importer / 'd3dx.ini', stop), [])

    def test_stop_during_collection_ends_scanning(self):
        for index in range(30):
            self.write(f'Mods/Many/{index:02d}/mod.ini', '[ResourceX]\nfilename = x.buf\n')
        stop = threading.Event()
        parsed = []
        original = mod_prefetch._referenced_files

        def parse_then_stop(path, stop_event=None):
            parsed.append(path)
            if len(parsed) == 2:
                stop.set()
            return original(path, stop_event)

        with mock.patch.object(mod_prefetch, '_referenced_files', parse_then_stop):
            mod_prefetch.prefetch_files(mod_prefetch.iter_mod_files(self.importer / 'd3dx.ini', stop), 10 ** 9, stop)
        self.assertLessEqual(len(parsed), 3)

    def test_exhausted_budget_ends_collection(self):
        for index in range(30):
            self.write(f'Mods/Many/{index:02d}/mod.ini', '[ResourceX]\n' + 'x' * 1000 + '\n')
        parsed = []
        original = mod_prefetch._referenced_files
        with mock.patch.object(mod_prefetch, '_referenced_files',
                               lambda path, stop=None: (parsed.append(path), original(path, stop))[1]):
            stats = mod_prefetch.prefetch_files(mod_prefetch.iter_mod_files(self.importer / 'd3dx.ini'), budget=1)
        self.assertLessEqual(len(parsed), 2)
        self.assertFalse(stats.completed)

    def test_replaced_run_stops_collecting(self):
        started, release = threading.Event(), threading.Event()
        original = mod_prefetch._referenced_files

        def blocking(path, stop=None):
            started.set()
            release.wait(5)
            return original(path, stop)

        with mock.patch.object(mod_prefetch, '_referenced_files', blocking):
            first = mod_prefetch.ModPrefetcher.start(self.importer / 'd3dx.ini', budget=10 ** 9)
            self.assertTrue(started.wait(5))
            second = mod_prefetch.ModPrefetcher.start(self.importer / 'd3dx.ini', budget=10 ** 9)
            release.set()
            first.thread.join(5)
            second.thread.join(5)
        self.assertFalse(first.thread.is_alive())
        self.assertFalse(first.stats.completed)
        self.assertTrue(second.stats.completed)

    def test_oversized_ini_line_is_skipped_with_bounded_reads(self):
        self.write('Mods/Huge/mod.ini', 'x' * (mod_prefetch.MAX_LINE_BYTES * 3) + '\nfilename = after.buf\n')
        reads = []
        real_open = open

        class Recording:
            def __init__(self, file):
                self.file = file

            def readline(self, limit=-1):
                line = self.file.readline(limit)
                reads.append(len(line))
                return line

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.file.close()

        with mock.patch('builtins.open', lambda path, mode='r', *a, **k: Recording(real_open(path, mode, *a, **k))):
            found = list(mod_prefetch._referenced_files(str(self.importer / 'Mods/Huge/mod.ini')))
        self.assertTrue(found[0].endswith('after.buf'))
        self.assertLessEqual(max(reads), mod_prefetch.MAX_LINE_BYTES)


    def test_over_budget_ini_is_not_parsed(self):
        self.write('Mods/Big/mod.ini', '[ResourceA]\n' + 'filename = x.buf\n' * 2000)
        readlines = []
        real_open = open

        class Counting:
            def __init__(self, file):
                self.file = file

            def readline(self, limit=-1):
                readlines.append(limit)
                return self.file.readline(limit)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.file.close()

        settings = self.importer / 'only-big.ini'
        settings.write_text('[Include]\ninclude_recursive = Mods/Big\n', encoding='utf-8')
        with mock.patch('builtins.open', lambda path, mode='r', *a, **k: Counting(real_open(path, mode, *a, **k))):
            stats = mod_prefetch.prefetch_files(mod_prefetch.iter_mod_files(settings), budget=1)
        self.assertEqual(stats.bytes, 0)
        self.assertEqual(readlines, [])

    def test_stop_interrupts_an_oversized_line(self):
        path = self.write('Mods/Long/mod.ini', 'x' * (mod_prefetch.MAX_LINE_BYTES * 3) + '\nfilename = after.buf\n')
        stop = threading.Event()
        reads = []
        real_open = open

        class StopAfterFirstRead:
            def __init__(self, file):
                self.file = file

            def readline(self, limit=-1):
                reads.append(limit)
                stop.set()
                return self.file.readline(limit)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.file.close()

        with mock.patch('builtins.open', lambda p, mode='r', *a, **k: StopAfterFirstRead(real_open(p, mode, *a, **k))):
            found = list(mod_prefetch._referenced_files(str(path), stop))
        self.assertEqual(found, [])
        self.assertEqual(len(reads), 1)

    def test_setting_switches_prefetch_off(self):
        with mock.patch.object(mod_prefetch.ModPrefetcher, 'start') as start:
            self.assertIsNone(mod_prefetch.ModPrefetcher.start_for_importer(self.importer, enabled=False))
            self.assertIsNone(mod_prefetch.ModPrefetcher.start_for_importer(self.importer / 'missing', enabled=True))
            start.assert_not_called()
            mod_prefetch.ModPrefetcher.start_for_importer(self.importer, enabled=True)
            start.assert_called_once_with(self.importer / 'd3dx.ini')


if __name__ == '__main__':
    unittest.main()
