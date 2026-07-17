# ============================================================================
#  ticks.py - rollover-safe millisecond timing helpers
# ============================================================================
#  Purpose:   Wraps supervisor.ticks_ms() with the wraparound-safe arithmetic
#             every module uses for scheduling (UI tick, touch poll, stale
#             timeouts, log interval).
#  Talks to:  The CircuitPython supervisor (no external hardware).
#  Fits in:   canbus.py / ui.py / touch.py / datalog.py all schedule off one
#             ticks.ms() timestamp taken per main-loop pass in code.py.
#
#  Why not time.monotonic()? It returns a float whose precision decays the
#  longer the board stays powered (a SAMD51 float loses sub-millisecond
#  resolution within hours and whole milliseconds within days) - bad for a
#  dash that lives on a car battery. supervisor.ticks_ms() is a uint that
#  wraps at 2**29 ms (~6.2 days); the diff() below is written so comparisons
#  stay correct ACROSS that wrap, as long as the two timestamps being
#  compared are less than ~3.1 days apart - always true for the sub-second
#  intervals used here. This is the pattern Adafruit's own docs recommend.
# ============================================================================

import supervisor

# ==== WRAP CONSTANTS (from the CircuitPython supervisor documentation) ======
_TICKS_PERIOD = 1 << 29
_TICKS_MAX = _TICKS_PERIOD - 1
_TICKS_HALFPERIOD = _TICKS_PERIOD // 2


def ms():
    """Current millisecond tick count (int, wraps at 2**29).

    Returns: int in [0, 2**29). Allocation-free (small-int arithmetic).
    Take ONE reading per main-loop pass and hand it to every subsystem so
    all of them agree on "now".
    """
    return supervisor.ticks_ms()


def diff(a, b):
    """Signed milliseconds from tick ``b`` to tick ``a`` (i.e. a - b).

    Parameters: a, b - values previously returned by ms().
    Returns: int, positive when `a` is later than `b`, correct across the
    2**29 wrap provided the real gap is under ~3.1 days.

    Always compare times as ``diff(now, then) >= interval`` - NEVER as
    ``now - then`` or ``now >= deadline``, both of which break at the wrap.
    """
    d = (a - b) & _TICKS_MAX
    return ((d + _TICKS_HALFPERIOD) & _TICKS_MAX) - _TICKS_HALFPERIOD
