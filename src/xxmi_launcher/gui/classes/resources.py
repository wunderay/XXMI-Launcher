import os

from collections import OrderedDict

from core.utils.lazy_module import LazyModule  # noqa: F401 (re-exported)


class LazyMarkdown:
    """GitHub-flavored Markdown parser built on first `convert` (markdown and its extensions
    are only needed once a message, tooltip or settings description is rendered)."""

    def __init__(self):
        self._parser = None

    def convert(self, text: str) -> str:
        if self._parser is None:
            import markdown
            from mdx_gfm import GithubFlavoredMarkdownExtension
            self._parser = markdown.Markdown(extensions=[GithubFlavoredMarkdownExtension()])
        return self._parser.convert(text)


class PreparedImageCache:
    """Bounded LRU of resized/adjusted static theme images.

    The same icons and backgrounds are prepared many times (hover/selection states, repeated
    widgets), and each preparation decodes, converts and LANCZOS-resizes the file again. Only
    file-backed static images are cached, keyed by file identity and all preparation inputs.
    Cached images are shared and must never be modified in place (PhotoImage only reads them).
    """

    def __init__(self, max_bytes: int = 32 * 1024 * 1024, max_entry_bytes: int = 1024 * 1024):
        self.max_bytes = max_bytes
        # Large backgrounds are prepared once per window; holding a copy only costs memory
        self.max_entry_bytes = max_entry_bytes
        self.used_bytes = 0
        self.entries: OrderedDict = OrderedDict()

    @staticmethod
    def key(image, size: tuple[int, int], opacity: float, brightness: float):
        filename = getattr(image, 'filename', None)
        if not filename:
            return None
        try:
            stat = os.stat(filename)
        except OSError:
            return None
        return filename, stat.st_mtime_ns, stat.st_size, size, opacity, brightness

    def get(self, key):
        image = self.entries.get(key)
        if image is not None:
            self.entries.move_to_end(key)
        return image

    def put(self, key, image):
        size = image.width * image.height * 4
        if size > min(self.max_bytes, self.max_entry_bytes):
            return
        old = self.entries.pop(key, None)
        if old is not None:
            self.used_bytes -= old.width * old.height * 4
        self.entries[key] = image
        self.used_bytes += size
        while self.used_bytes > self.max_bytes:
            _, evicted = self.entries.popitem(last=False)
            self.used_bytes -= evicted.width * evicted.height * 4


class PreparedImageDiskCache:
    """Prepared large images (window backgrounds) stored as lossless WebP between launches.

    Decoding a full-size theme background and LANCZOS-resizing it costs ~75 ms per image on every
    start; loading the already prepared, pixel-identical result costs ~16 ms. Entries are keyed
    by the same inputs as PreparedImageCache plus the Pillow version, written on a background
    thread, and only the newest `max_files` are kept.
    """

    def __init__(self, folder, max_files: int = 16):
        self.folder = folder
        self.max_files = max_files

    def path(self, key):
        import hashlib
        import PIL
        digest = hashlib.sha1(repr((key, PIL.__version__)).encode('utf-8')).hexdigest()
        return self.folder / f'{digest}.webp'

    def load(self, key):
        from PIL import Image
        path = self.path(key)
        try:
            with Image.open(path) as image:
                image.load()
                return image.convert('RGBA')
        except Exception:
            return None

    def store_async(self, key, image):
        import threading
        threading.Thread(target=self._store, args=(key, image), name='ImageCacheWrite', daemon=True).start()

    def _store(self, key, image):
        path = self.path(key)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.tmp')
            image.save(temporary, 'WEBP', lossless=True, method=0, exact=True)
            os.replace(temporary, path)
            entries = sorted(self.folder.glob('*.webp'), key=lambda entry: entry.stat().st_mtime, reverse=True)
            for stale in entries[self.max_files:]:
                stale.unlink(missing_ok=True)
            # Writes interrupted by the launcher exiting
            for leftover in self.folder.glob('*.webp.tmp'):
                if leftover != temporary and leftover.stat().st_mtime < os.path.getmtime(path) - 60:
                    leftover.unlink(missing_ok=True)
        except Exception:
            pass
