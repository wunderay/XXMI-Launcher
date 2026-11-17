import os
import importlib

from collections import OrderedDict


class LazyModule:
    """Imports a module on first attribute access, so optional heavy libraries cost nothing
    (import time, memory) until a feature actually uses them."""

    def __init__(self, name: str):
        self._name = name
        self._module = None

    def __getattr__(self, attr):
        if self._module is None:
            self._module = importlib.import_module(self._name)
        return getattr(self._module, attr)


class PreparedImageCache:
    """Bounded LRU of resized/adjusted static theme images.

    The same icons and backgrounds are prepared many times (hover/selection states, repeated
    widgets), and each preparation decodes, converts and LANCZOS-resizes the file again. Only
    file-backed static images are cached, keyed by file identity and all preparation inputs.
    Cached images are shared and must never be modified in place (PhotoImage only reads them).
    """

    def __init__(self, max_bytes: int = 32 * 1024 * 1024):
        self.max_bytes = max_bytes
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
        if size > self.max_bytes:
            return
        old = self.entries.pop(key, None)
        if old is not None:
            self.used_bytes -= old.width * old.height * 4
        self.entries[key] = image
        self.used_bytes += size
        while self.used_bytes > self.max_bytes:
            _, evicted = self.entries.popitem(last=False)
            self.used_bytes -= evicted.width * evicted.height * 4
