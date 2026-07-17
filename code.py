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
#  Project: https://github.com/<you>/megasquirt-can-dash  (see README.md)
# ============================================================================

import gc

import board

import config
import ticks
import canbus
import datalog
import touch
import ui

# ==== STARTUP (order matters) ===============================================
# 1. Display first - it shows the boot splash while everything else builds,
#    and constructing DashUI claims the shared SPI bus via board.SPI().
dash = ui.DashUI()

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
logger = datalog.DataLogger(board.SPI())

# All long-lived objects now exist. Collect the startup garbage once so the
# loop begins with a defragmented heap; steady-state allocation is near zero
# (see PERFORMANCE.md), so further collections are rare and tiny.
gc.collect()
if config.DEBUG:
    print("startup complete, mem_free:", gc.mem_free())

# ==== DEBUG INSTRUMENTATION STATE ===========================================
# Plain ints, only touched when config.DEBUG is True - a single false `if`
# per loop otherwise.
_dbg_window_start = ticks.ms()
_dbg_prev = _dbg_window_start
_dbg_loops = 0
_dbg_worst_ms = 0

# ==== MAIN LOOP =============================================================
while True:
    now = ticks.ms()          # one timestamp per pass - all subsystems agree

    can.update(ecu, now)      # drain every pending CAN frame (never blocks)

    delta = nav.update(now)   # 50 Hz touch poll; -1/0/+1 page step
    if delta:
        dash.change_page(delta)

    dash.update(ecu, now, can.bus_ok, logger.state)   # ~20 Hz render tick

    logger.update(ecu, now)   # engine-gated 10 Hz CSV rows

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
            )
            _dbg_window_start = now
            _dbg_loops = 0
            _dbg_worst_ms = 0
