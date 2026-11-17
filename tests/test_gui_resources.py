import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gui.classes.resources import LazyModule, PreparedImageCache, PreparedImageDiskCache
from gui.classes import fonts


class LazyModuleTests(unittest.TestCase):
    def test_imports_only_on_first_attribute_access(self):
        name = 'json.tool'
        sys.modules.pop(name, None)

        def load():
            import json.tool
            return json.tool

        module = LazyModule(load)
        self.assertNotIn(name, sys.modules)
        self.assertTrue(callable(module.main))
        self.assertIn(name, sys.modules)


class FakeImage:
    def __init__(self, width, height, filename=None):
        self.width, self.height = width, height
        if filename:
            self.filename = filename


class PreparedImageCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name) / 'icon.png'
        self.file.write_bytes(b'png')

    def test_key_covers_file_identity_and_every_preparation_input(self):
        image = FakeImage(4, 4, str(self.file))
        key = PreparedImageCache.key(image, (8, 8), 1, 1)
        self.assertIsNotNone(key)
        self.assertNotEqual(key, PreparedImageCache.key(image, (8, 9), 1, 1))
        self.assertNotEqual(key, PreparedImageCache.key(image, (8, 8), 0.5, 1))
        self.assertNotEqual(key, PreparedImageCache.key(image, (8, 8), 1, 0.5))
        stat = self.file.stat()
        os.utime(self.file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertNotEqual(key, PreparedImageCache.key(image, (8, 8), 1, 1))

    def test_generated_and_missing_images_are_not_cached(self):
        self.assertIsNone(PreparedImageCache.key(FakeImage(4, 4), (4, 4), 1, 1))
        self.assertIsNone(PreparedImageCache.key(FakeImage(4, 4, str(self.file) + '.gone'), (4, 4), 1, 1))

    def test_least_recently_used_entries_are_evicted_within_budget(self):
        cache = PreparedImageCache(max_bytes=3 * 10 * 10 * 4, max_entry_bytes=10 * 10 * 4)
        for name in 'abc':
            cache.put(name, FakeImage(10, 10))
        self.assertIsNotNone(cache.get('a'))  # refresh "a"
        cache.put('d', FakeImage(10, 10))
        self.assertIsNone(cache.get('b'))
        self.assertIsNotNone(cache.get('a'))
        self.assertLessEqual(cache.used_bytes, cache.max_bytes)
        cache.put('huge', FakeImage(11, 10))
        self.assertIsNone(cache.get('huge'))


class StaticImagePreparationTests(unittest.TestCase):
    """widgets._prepare_static_image keys its cache by every input that changes the result."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name) / 'icon.png'
        self.file.write_bytes(b'png')
        source = Path(__file__).resolve().parents[1] / 'src' / 'xxmi_launcher' / 'gui' / 'classes' / 'widgets.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_prepare_static_image')
        disk = SimpleNamespace(load=lambda key: None, store_async=lambda key, image: None)
        namespace = {'Image': SimpleNamespace(Image=object), '_prepared_images': PreparedImageCache(),
                     '_disk_cache': lambda: disk, '_StaticImageSource': type('Source', (), {})}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        self.prepare_static = namespace['_prepare_static_image']

    def widget(self, opacity=1, brightness=1, width=8):
        return SimpleNamespace(opacity=opacity, brightness=brightness, _width=width, _height=8,
                               _apply_widget_scaling=lambda value: value,
                               _prepare_image=lambda image: FakeImage(width, 8))

    def test_widgets_differing_in_any_input_get_their_own_result(self):
        image = FakeImage(4, 4, str(self.file))
        first = self.prepare_static(self.widget(), image)
        self.assertIs(self.prepare_static(self.widget(), image), first)
        for other in (self.widget(opacity=0.5), self.widget(brightness=0.5), self.widget(width=9)):
            self.assertIsNot(self.prepare_static(other, image), first)


class PreparedImageDiskCacheTests(unittest.TestCase):
    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is not installed')
        self.Image = Image
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cache = PreparedImageDiskCache(Path(temp.name) / 'Images', max_files=2)

    def test_round_trip_is_pixel_identical_including_transparent_colors(self):
        image = self.Image.new('RGBA', (64, 32), (10, 20, 30, 0))
        image.putpixel((5, 5), (200, 100, 50, 128))
        self.cache._store(('a', 1), image)
        loaded = self.cache.load(('a', 1))
        self.assertEqual(loaded.mode, 'RGBA')
        self.assertEqual(loaded.tobytes(), image.tobytes())
        self.assertIsNone(self.cache.load(('a', 2)))

    def test_only_newest_entries_are_kept(self):
        for index in range(4):
            self.cache._store(('k', index), self.Image.new('RGBA', (4, 4), (index, 0, 0, 255)))
            path = self.cache.path(('k', index))
            os.utime(path, (1000 + index, 1000 + index))
        self.assertEqual(len(list(self.cache.folder.glob('*.webp'))), 2)


class PrivateFontTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.font = Path(temp.name) / 'Theme.ttf'
        self.font.write_bytes(b'\0\1\0\0font-bytes')

    def test_registers_font_bytes_with_gdi_like_pyglet(self):
        calls = []

        def add_font(data, size, reserved, count):
            calls.append((bytes(data), size, reserved))
            count._obj.value = 1
            return 1234

        gdi32 = SimpleNamespace(AddFontMemResourceEx=mock.Mock(side_effect=add_font))
        with mock.patch.object(fonts.sys, 'platform', 'win32'), \
                mock.patch.object(fonts.ctypes, 'WinDLL', create=True, return_value=gdi32), \
                mock.patch.object(fonts.ctypes, 'get_last_error', create=True, return_value=0):
            self.assertEqual(fonts.register_private_font(self.font), 1)
        self.assertEqual(calls, [(self.font.read_bytes(), len(self.font.read_bytes()), None)])

    def test_failed_registration_raises(self):
        gdi32 = SimpleNamespace(AddFontMemResourceEx=mock.Mock(return_value=None))
        with mock.patch.object(fonts.sys, 'platform', 'win32'), \
                mock.patch.object(fonts.ctypes, 'WinDLL', create=True, return_value=gdi32), \
                mock.patch.object(fonts.ctypes, 'get_last_error', create=True, return_value=8), \
                mock.patch.object(fonts.ctypes, 'WinError', create=True, side_effect=lambda code: OSError(code)):
            with self.assertRaises(OSError):
                fonts.register_private_font(self.font)

    def test_other_platforms_skip_registration(self):
        with mock.patch.object(fonts.sys, 'platform', 'linux'):
            self.assertEqual(fonts.register_private_font(self.font), 0)


if __name__ == '__main__':
    unittest.main()
