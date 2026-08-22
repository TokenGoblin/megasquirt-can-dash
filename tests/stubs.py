# ============================================================================
#  tests/stubs.py - make the dash modules importable on a desktop Python
# ============================================================================
#  Purpose:   Installs throwaway stand-ins for the CircuitPython-only modules
#             (board, canio, displayio, ...) into sys.modules, then puts the
#             repo root on the path, so `import canbus` works under CPython.
#  Talks to:  Nothing. No hardware is touched and none is simulated - the
#             stubs exist only to satisfy import statements.
#  Fits in:   Imported FIRST by every test module, before any dash import.
#
#  Why this works at all: none of the dash modules call into hardware at
#  import time. They compute constants from config.py and define classes; the
#  hardware only gets touched inside __init__ methods the tests never run
#  (they build objects with __new__ and set the few attributes they need).
#  So the stubs can be inert - if a test ever starts failing with a weird
#  attribute error from in here, that is the signal that some module started
#  doing real work at import time, which is itself worth knowing.
#
#  NOTE the sys.path.append (not insert) below: this repo contains a code.py,
#  which would shadow the standard library's `code` module for anything that
#  imports it (pdb does, so pyflakes and pytest do). Appending keeps the
#  stdlib ahead of the repo. Run the suite as `python tests/run_tests.py`.
# ============================================================================

import os
import sys
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Anything:
    """Permissive placeholder: any attribute, call, or index yields another
    one. Enough to satisfy `board.D9`, `canio.BusState.ERROR_ACTIVE`, and the
    like without pretending to behave like the real thing."""

    def __init__(self, name="stub"):
        self._name = name

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        child = _Anything("{}.{}".format(self._name, attr))
        setattr(self, attr, child)
        return child

    def __call__(self, *args, **kwargs):
        return _Anything(self._name + "()")

    def __getitem__(self, key):
        return _Anything("{}[{}]".format(self._name, key))

    def __setitem__(self, key, value):
        pass

    def __repr__(self):
        return "<stub {}>".format(self._name)


def _install(name):
    """Register an inert module under `name` (and as an attribute of its
    parent package, so `from pkg import sub` resolves)."""
    module = types.ModuleType(name)

    def _module_getattr(attr, _module=module, _name=name):
        if attr.startswith("__"):
            raise AttributeError(attr)
        value = _Anything("{}.{}".format(_name, attr))
        setattr(_module, attr, value)
        return value

    module.__getattr__ = _module_getattr
    sys.modules[name] = module
    if "." in name:
        parent, _, child = name.rpartition(".")
        setattr(sys.modules[parent], child, module)
    return module


_STUBBED = (
    "board",
    "canio",
    "digitalio",
    "storage",
    "supervisor",
    "displayio",
    "fourwire",
    "terminalio",
    "bitmaptools",
    "adafruit_sdcard",
    "adafruit_ili9341",
    "adafruit_tsc2007",
    "adafruit_display_text",
    "adafruit_display_text.bitmap_label",
)


def install():
    """Install every stub and put the repo root on sys.path. Idempotent."""
    for name in _STUBBED:
        if name not in sys.modules:
            _install(name)
    # supervisor.ticks_ms is the one stub with real behavior: ticks.py calls
    # it, and the tests drive time by setting supervisor.ticks_ms directly.
    sys.modules["supervisor"].ticks_ms = lambda: 0
    if _REPO_ROOT not in sys.path:
        sys.path.append(_REPO_ROOT)   # append: never shadow stdlib `code`


install()
