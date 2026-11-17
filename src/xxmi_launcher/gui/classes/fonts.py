import sys
import ctypes
import logging

from pathlib import Path

log = logging.getLogger(__name__)


def register_private_font(font_path: Path) -> int:
    """Make a TTF file usable by Tk in this process only.

    Same GDI call pyglet's `font.add_file` makes with `win32_gdi_font` (AddFontMemResourceEx;
    GDI copies the data, so the buffer may be freed), without importing pyglet, which loads its
    OpenGL/image stack only to do this. Returns the number of fonts added.
    """
    if sys.platform != 'win32':
        # Tk does not use GDI here; theme fonts fall back to system fonts on unsupported platforms
        log.debug(f'Skipped private font registration on {sys.platform}: {font_path}')
        return 0
    data = Path(font_path).read_bytes()
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
    gdi32.AddFontMemResourceEx.restype = ctypes.c_void_p
    gdi32.AddFontMemResourceEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_uint32)]
    fonts_added = ctypes.c_uint32()
    handle = gdi32.AddFontMemResourceEx(data, len(data), None, ctypes.byref(fonts_added))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return fonts_added.value
