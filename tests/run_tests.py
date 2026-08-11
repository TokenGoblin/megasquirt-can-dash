#!/usr/bin/env python3
# ============================================================================
#  tests/run_tests.py - run the whole suite on a desktop Python
# ============================================================================
#  Usage:  python tests/run_tests.py          (from anywhere)
#          python tests/run_tests.py -v       (per-test names)
#
#  No pytest, no dependencies, no hardware: stubs.py stands in for the
#  CircuitPython modules and every test exercises the real dash code.
#
#  Run it this way rather than `python -m unittest` from the repo root: this
#  repo has a code.py at its root (CircuitPython requires that name), which
#  shadows the standard library's `code` module for any tool that imports it.
#  Launching from this directory keeps the repo root off the front of the
#  path - see stubs.py.
# ============================================================================

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stubs  # noqa: E402

stubs.install()   # stubs + repo path, before any dash import


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    verbosity = 2 if "-v" in sys.argv else 1
    suite = unittest.defaultTestLoader.discover(here, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
