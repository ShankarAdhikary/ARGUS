"""Swap the real numpy/scipy/faiss in for a test.

conftest.py stubs numpy, scipy and faiss for the whole suite (the real ones are heavy and most tests don't need them). A few
tests do need the real thing. numpy's C extension cannot be imported twice in one process, so each real library is loaded once,
on first request, and cached here; every test module that needs them must go through this helper (not keep its own cache).
"""

import importlib
import sys
from contextlib import contextmanager

import pytest

_REAL: dict = {}      # module name -> real module, filled per library on demand
_LOADED: set = set()
_LIBS = ("numpy", "scipy", "faiss")
_is_lib = lambda k: k.split(".")[0] in _LIBS


def _load(root: str, probe: str) -> None:
    """Import the real `root` (testing `probe` so submodules come with it) once, remembering its modules."""
    if root in _LOADED:
        return
    try:
        importlib.import_module(probe)
    except ImportError:
        pytest.skip(f"{root} not installed")
    _REAL.update({k: v for k, v in sys.modules.items() if k.split(".")[0] == root})
    _LOADED.add(root)


@contextmanager
def real_libs(scipy: bool = True, faiss: bool = False):
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if _is_lib(k)}
    try:
        sys.modules.update({k: v for k, v in _REAL.items() if k.split(".")[0] in ("numpy", "scipy") or k.split(".")[0] in _LOADED})
        _load("numpy", "numpy")
        if scipy:
            _load("scipy", "scipy.stats")
        if faiss:
            _load("faiss", "faiss")
        sys.modules.update(_REAL)
        yield
    finally:
        for k in [k for k in sys.modules if _is_lib(k)]:
            sys.modules.pop(k)
        sys.modules.update(saved)
