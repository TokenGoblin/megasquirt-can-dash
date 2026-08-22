# ============================================================================
#  code.py - main entry point: wire the subsystems together and loop
# ============================================================================
#  Purpose:   Thin cooperative main loop for the MegaSquirt CAN digital dash.
#             All real work lives in the modules; this file only constructs
#             them, then cycles: CAN drain -> touch poll -> render -> datalog.
#  Talks to:  Nothing directly - each subsystem owns its own hardware:
#               canbus.py  - SAME51 CAN peripheral (MegaSquirt broadcast in)
#               ui.py      - ILI9341 TFT over SPI (all rendering)
#               touch.py   - TSC2007 touch over I2C (page navigation)
#               datalog.py - microSD over shared SPI (CSV logging)
#             config.py holds every tunable; ticks.py the timing helpers.
#  Fits in:   This IS the top: CircuitPython auto-runs code.py at power-up.
#
#  Loop model: strictly non-blocking and cooperative. No time.sleep() ever
#  runs here; each update() self-schedules off one shared ticks.ms() stamp
#  per pass (UI ~20 Hz, touch 50 Hz, datalog 10 Hz, CAN drained every pass),
#  so the loop spins at several hundred Hz and every subsystem hits its own
#  deadline independently. See PERFORMANCE.md for the full design rationale.
#
#  Failure model: the loop catches every exception rather than letting one
#  end the VM - a dash that stops mid-drive doesn't come back without a power
#  cycle, and losing the SD card must never cost you the gauges. Faults show
#  as a FLT counter on the panel; only sustained failure (see
#  config.MAX_FAULTS_BEFORE_HALT) halts on the red screen. An optional
#  hardware watchdog covers the other case - a hang rather than a raise.
#
#  Project: https://github.com/TokenGoblin/megasquirt-can-dash  (see README.md)
# ============================================================================

import gc

import board

import config
import ticks
import canbus
import datalog
import touch
import ui

# Watchdog support is port-dependent - not every CircuitPython build exposes
# microcontroller.watchdog. Imported here (top of file, per the project's
# no-lazy-imports rule) but guarded, so a board without it still boots.
try:
    import microcontroller
    from watchdog import WatchDogMode
except ImportError:
    microcontroller = None
    WatchDogMode = None

# ==== STARTUP (order matters) ===============================================
# 0. Check config.py BEFORE touching hardware, but hold the verdict until
#    there's a screen to put it on - a settings mistake should announce
#    itself at startup, by name, not divide by zero three pages later.
#    Guarded, because validate() reads the very structures it is checking:
#    an entry of the wrong SHAPE (rather than merely the wrong value) can
#    make the validator itself raise, and an unhandled raise here - before
#    any display exists - is the black screen this whole function exists to
#    prevent.
try:
    _config_error = config.validate()
except Exception as e:  # pylint: disable=broad-except
    print("config.validate() could not run - config.py is malformed:", e)
    _config_error = "CFG: MALFORMED"

# Hand the validator's memory back before anything else allocates. It has
# done its whole job for this power cycle, and its error messages are written
# for a human reading a serial console - which makes them, measured on the
# board, about 6 KB of strings that would otherwise sit in the heap for the
# entire drive. That is a quarter of what the display needs to build its
# widgets, and this dash ran out of memory constructing them before this
# existed. Diagnostics you can afford are better than diagnostics you can't.
try:
    del config.validate
except (AttributeError, NameError):
    pass
gc.collect()

# 1. Display first - it comes up blank in well under a second and gives
#    every later failure somewhere to report itself. Constructing DashUI
#    also claims the shared SPI bus via board.SPI().
dash = ui.DashUI()
if _config_error:
    dash.show_fatal(_config_error)   # never returns; details went to serial

# 2. Live-data store + CAN. A dash with no CAN peripheral is useless, so
#    that failure gets the red fatal screen (which never returns).
ecu = canbus.EcuData()
try:
    can = canbus.CanBus()
except Exception:  # pylint: disable=broad-except
    dash.show_fatal("CAN INIT FAILED")

# 3. Touch + datalogger degrade gracefully (no touch chip = no navigation,
#    no SD card = no logging) - both report and carry on.
nav = touch.TouchNav()
dash.set_touch_available(nav.available)
logger = datalog.DataLogger(board.SPI())


# 4. Watchdog, armed LAST - after all the slow construction above, which
#    would trip it. Disabled by default; see config.WATCHDOG_TIMEOUT_S for
#    why and when to turn it on.
def _arm_watchdog():
    """Return a fed-per-pass watchdog, or None if unavailable/disabled."""
    if not config.WATCHDOG_TIMEOUT_S or microcontroller is None:
        return None
    try:
        wdt = microcontroller.watchdog
        wdt.timeout = config.WATCHDOG_TIMEOUT_S
        wdt.mode = WatchDogMode.RESET
        print("watchdog armed:", config.WATCHDOG_TIMEOUT_S, "s")
        return wdt
    except Exception as e:  # pylint: disable=broad-except
        # Unsupported port, bad timeout value, already-running timer: none of
        # these are worth refusing to run a dash over.
        print("watchdog unavailable - continuing without it:", e)
        return None


# All long-lived objects now exist. Collect the startup garbage once so the
# loop begins with a defragmented heap; steady-state allocation is near zero
# (see PERFORMANCE.md), so further collections are rare and tiny.
gc.collect()

# Armed only now: everything above (display construction, the SD probe,
# this collection) is slower than any sane watchdog timeout.
wdt = _arm_watchdog()

if config.DEBUG:
    print("startup complete, mem_free:", gc.mem_free())

# ==== DEBUG INSTRUMENTATION STATE ===========================================
# Plain ints, only touched when config.DEBUG is True - a single false `if`
# per loop otherwise.
_dbg_window_start = ticks.ms()
_dbg_prev = _dbg_window_start
_dbg_loops = 0
_dbg_worst_ms = 0

# ==== FAULT CONTAINMENT STATE ===============================================
# The loop below catches everything. Rationale: every subsystem here is
# optional EXCEPT the display - a dead SD card, a glitching touch chip, or a
# transient bus error must cost you that subsystem, never your gauges. An
# uncaught exception ends the CircuitPython VM, and a dash that stops at
# 70 mph doesn't come back without a power cycle.
_faults = 0
_last_fault = 0

# Last page step a tap made, so a hold on that same press can undo it.
_last_delta = 0

# ==== MAIN LOOP =============================================================
while True:
    try:
        now = ticks.ms()      # one timestamp per pass - all subsystems agree

        can.update(ecu, now)  # drain every pending CAN frame (never blocks)

        delta = nav.update(now)   # 50 Hz touch poll; -1/0/+1 step, or HOLD
        if delta == touch.HOLD:
            # Held: clear the peak/low/average readouts, and undo the page
            # step this same press already fired on its press edge - taps
            # deliberately act immediately, so a hold has to put the page
            # back itself. The readouts snapping to "PEAK ---" is the
            # confirmation.
            ecu.reset_stats()
            if _last_delta:
                dash.change_page(-_last_delta)
                _last_delta = 0
        elif delta:
            dash.change_page(delta)
            _last_delta = delta

        dash.update(ecu, now, can.bus_ok, logger.state,   # ~20 Hz render tick
                    can.baro_ref_x10, can.baro_locked)

        logger.update(ecu, now)   # engine-gated 10 Hz CSV rows

        if wdt is not None:
            wdt.feed()        # only ever reached by a pass that completed

        # A quiet spell means whatever it was has passed: forget it and drop
        # the indicator. Gated on _faults so a healthy loop does one int test.
        if _faults and ticks.diff(now, _last_fault) >= config.FAULT_CLEAR_MS:
            _faults = 0
            dash.clear_fault()

        if config.DEBUG:
            _dbg_loops += 1
            _dt = ticks.diff(now, _dbg_prev)
            _dbg_prev = now
            if _dt > _dbg_worst_ms:
                _dbg_worst_ms = _dt
            if ticks.diff(now, _dbg_window_start) >= config.DEBUG_INTERVAL_MS:
                print(
                    "loops/s:", _dbg_loops * 1000 // config.DEBUG_INTERVAL_MS,
                    "worst_loop_ms:", _dbg_worst_ms,
                    "mem_free:", gc.mem_free(),
                    "faults:", _faults,
                    "baro_x10:", can.baro_ref_x10,
                    "boost_x10:", ecu.value_x10[config.PARAM_BOOST],
                )
                _dbg_window_start = now
                _dbg_loops = 0
                _dbg_worst_ms = 0

    except Exception as e:  # pylint: disable=broad-except
        _faults += 1
        _last_fault = ticks.ms()
        print("loop fault", _faults, ":", e)

        if _faults >= config.MAX_FAULTS_BEFORE_HALT:
            # Failing continuously, not glitching. Stop pretending: halt on
            # the red screen so the driver sees something unambiguous rather
            # than gauges that may or may not be updating. Disarm the watchdog
            # first if we can - otherwise it resets the board out of the fatal
            # screen and the dash boot-loops instead of holding the message.
            if wdt is not None:
                try:
                    wdt.deinit()
                except Exception:  # pylint: disable=broad-except
                    pass
            dash.show_fatal("LOOP FAULT")   # never returns

        # Surface the count on the panel. Wrapped because the display is
        # exactly what might be broken, and failing to report a fault must
        # not itself become the fault that halts the dash.
        try:
            dash.note_fault(_faults)
        except Exception:  # pylint: disable=broad-except
            pass
