from typing import Callable


class LazyModule:
    """Imports a module on first attribute access, so optional heavy libraries cost nothing
    (import time, memory) until a feature actually uses them.

    The module comes from `loader`, a function containing an ordinary `import` statement:
    frozen-build tools (PyInstaller, Nuitka) only bundle modules they can see imported in
    the code, and a module named only in a string would be left out of the release.
    """

    def __init__(self, loader: Callable[[], object]):
        self._loader = loader
        self._module = None

    def __getattr__(self, attr):
        if self._module is None:
            self._module = self._loader()
        return getattr(self._module, attr)
