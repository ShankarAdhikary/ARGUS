"""Swap the real numpy/scipy in for a test.

conftest.py stubs numpy and scipy for the whole suite (the real ones are heavy and most tests don't need them). A few tests
do need the real thing. numpy's C extension cannot be imported twice in one process, so the real modules are loaded once
and cached here; every test module that needs them must go through this helper (not keep its own cache).
"""

import importlib
import sys
from contextlib import contextmanager

import pytest

_REAL: dict = {}
_is_lib = lambda k: k.split(".")[0] in ("scipy", "numpy")


@contextmanager
def real_libs(scipy: bool = True):
    saved = {k: sys.modules.pop(k) for k in list(sys.modules) if _is_lib(k)}
    try:
        if _REAL:
            sys.modules.update(_REAL)
        else:
            try:
                importlib.import_module("scipy.stats" if scipy else "numpy")
            except ImportError:
                pytest.skip("numpy/scipy not installed")
            _REAL.update({k: v for k, v in sys.modules.items() if _is_lib(k)})
        yield
    finally:
        for k in [k for k in sys.modules if _is_lib(k)]:
            sys.modules.pop(k)
        sys.modules.update(saved)
