import importlib


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
