# ============================================================================
#  config.py - every user-tunable value for the MegaSquirt CAN dash
# ============================================================================
#  Purpose:   Single home for ALL tunables: CAN IDs/offsets/scales, alert
#             thresholds, colors, screen layout, timing intervals. No logic
#             lives here (a couple of derived tuples at the bottom aside) -
#             just data, so nothing in this file can slow the dash down.
#  Talks to:  Nothing directly. Imported by every other module. The only
#             hardware import is `board`, for pin name constants.
#  Fits in:   code.py (main loop) wires canbus.py / ui.py / touch.py /
#             datalog.py together; each of those reads its settings from here.
#
#  Every entry states its units, valid range, and where to find the right
#  value for YOUR setup. If you change hardware or ECU settings, this should
#  be the only file you need to touch.
# ============================================================================

import board

# ==== DEBUG INSTRUMENTATION =================================================

# Master debug switch. True prints loop rate, worst-case loop time, and free
# heap over USB serial every DEBUG_INTERVAL_MS. The check costs a single `if`
# per loop when False - effectively free. Leave False in the car.
DEBUG = False

# How often (milliseconds, > 0) the DEBUG stats line prints. Only matters
# when DEBUG is True.
DEBUG_INTERVAL_MS = 2000

# ==== CAN PROTOCOL: MegaSquirt "Simplified Dash Broadcasting" ===============
# The MS2/Extra firmware broadcasts dash data as four consecutive standard
# (11-bit) CAN IDs starting at a configurable base. All multi-byte fields are
# big-endian (Motorola byte order). Field layout confirmed against the
# official "Megasquirt CAN realtime data broadcast protocol" document
# (2016-02-17, EFI Analytics / msextra.com).

# Bus bit rate in bits/second. MegaSquirt broadcasts at 500 kbps; both ends
# of the bus must agree, so only change this if you changed it in the ECU.
CAN_BAUD_RATE = 500_000

# First CAN ID of the 4-frame broadcast block. TunerStudio: CAN-bus/Testmodes
# -> "Simplified Dash Broadcasting" -> "CAN ID (Base)". Default 1512 (0x5E8).
# Valid range for standard IDs: 0-2043 (base+3 must stay <= 2047).
BASE_CAN_ID = 1512

# --- Byte offsets and scale factors within each broadcast frame ------------
# real_value = raw_integer / 10 for every field here except RPM (raw = rpm).
# These match the published protocol - only change them if your firmware's
# broadcast layout genuinely differs (verify with a candump against known
# values: MAT/CLT should track ambient on a cold engine).
#
# Frame base+0: MAP kPa*10 (bytes 0-1) | RPM (2-3) | CLT degF*10 (4-5)
#               | TPS %*10 (6-7)
# Frame base+1: pw1 (0-1) | pw2 (2-3) | MAT degF*10 (4-5) | advance (6-7)
# Frame base+2: afrtgt1 (byte 0) | AFR1*10 (byte 1, SINGLE byte) | ...
# Frame base+3: battery V*10 (bytes 0-1) | sensors...
OFS_MAP = 0    # frame base+0, bytes 0-1, uint16 big-endian, kPa * 10
OFS_RPM = 2    # frame base+0, bytes 2-3, uint16 big-endian, rpm (x1)
OFS_CLT = 4    # frame base+0, bytes 4-5, int16  big-endian, deg F * 10 (signed: can read below zero)
OFS_TPS = 6    # frame base+0, bytes 6-7, int16  big-endian, % * 10
OFS_MAT = 4    # frame base+1, bytes 4-5, int16  big-endian, deg F * 10 (signed)
OFS_AFR1 = 1   # frame base+2, byte 1 ONLY, uint8, AFR * 10 (byte 0 is afrtgt1 - do not merge them)
OFS_BATT = 0   # frame base+3, bytes 0-1, int16  big-endian, Volts * 10

# Upper bound on CAN frames decoded per main-loop pass. The loop runs at
# hundreds of Hz and the ECU sends ~40-200 frames/s, so 16 comfortably drains
# any burst while bounding worst-case loop time if the bus is flooded.
CAN_MAX_FRAMES_PER_UPDATE = 16

# Milliseconds (>0) a channel may go without a fresh CAN frame before it is
# "stale": value shows "---" in red and the NO CAN banner appears.
STALE_TIMEOUT_MS = 1000

# ==== DERIVED CHANNELS ======================================================

# Local atmospheric pressure in kPa (float). Boost PSI is computed as
# (MAP - this) / KPA_PER_PSI, so tweak for your altitude if boost doesn't
# read ~0.0 with the engine off. Sea level standard: 101.3.
ATMOSPHERIC_KPA = 101.3

# Unit conversion constant (kPa per PSI). Physics - never needs changing.
KPA_PER_PSI = 6.894757

# ==== COLORS (24-bit 0xRRGGBB) ==============================================

COLOR_ALERT_GREEN = 0x00FF00
COLOR_ALERT_YELLOW = 0xFFFF00
COLOR_ALERT_RED = 0xFF0000
COLOR_ALERT_BLUE = 0x4080FF   # "cold"/idle blue used across several gauges

COLOR_BG = 0x000000           # page background
COLOR_TITLE = 0x00FFFF        # page name at the top
COLOR_VALUE = 0xFFFFFF        # big number (when no gradient applies)
COLOR_VALUE_STALE = 0xFF0000  # big number when data is stale
COLOR_UNITS = 0xFFFF00        # units line under the value
COLOR_PEAK = 0xFF8000         # "PEAK ###" readout
COLOR_DOT_ACTIVE = 0xFFFFFF   # current-page indicator dot
COLOR_DOT_INACTIVE = 0x404040 # other dots / unlit bar segments
COLOR_WARNING = 0xFF0000      # NO CAN / NO SD corner banners
COLOR_ARROW = 0x606060        # dim < > tap-zone hints
BAR_HOUSING_COLOR = 0x101010  # dark disc behind each shift-light bulb

# ==== ALERT THRESHOLDS / GRADIENT STOPS =====================================
# Each channel's color comes from a list of (value, color) "stops": flat
# color outside the ends, smooth blend between adjacent stops, and repeating
# a color at two stops makes a flat plateau. Values are in the channel's
# real display units.

# Coolant (deg F): blue below 160 (warming up), green 160-200 (normal),
# yellow at 200 fading to red by 230 (overheating). Edges blend over
# CLT_BLEND_BAND_F degrees instead of snapping.
CLT_BLUE_MAX_F = 160.0
CLT_GREEN_MAX_F = 200.0
CLT_RED_F = 230.0
CLT_BLEND_BAND_F = 10.0

# AFR: green at/richer than stoich, yellow to 15.5, red past that. Static
# thresholds (not load-gated) - tighten AFR_RED if you run leaner cruise AFRs.
AFR_YELLOW = 14.7
AFR_RED = 15.5

# Battery (Volts): two "normal" plateaus because a healthy battery reads
# differently engine-off vs engine-running:
#   <= 11.5 red (weak) | 12.2-12.6 blue (normal resting) |
#   13.0-14.7 green (normal charging) | >= 15.0 red (overcharging).
BATT_LOW_V = 11.5
BATT_COLD_MIN_V = 12.2
BATT_COLD_MAX_V = 12.6
BATT_RUN_MIN_V = 13.0
BATT_RUN_MAX_V = 14.7
BATT_HIGH_V = 15.0

# MAP (kPa): blue under vacuum, green under boost, blending across a band
# straddling atmospheric so it doesn't flicker at the boundary.
MAP_BLEND_BAND_KPA = 5.0

# RPM: continuous motorsport-style gradient - blue only at idle, then a
# smooth green -> yellow -> red climb. Match RPM_RED_AT to your redline.
RPM_IDLE_MAX = 1000
RPM_YELLOW_AT = 5300
RPM_RED_AT = 6100

# Boost (PSI): flat blue up through your boost target, fading to red at the
# "too much" ceiling.
BOOST_MIN_PSI = 0.0
BOOST_BLUE_MAX_PSI = 18.0
BOOST_MAX_PSI = 21.0

# MAT / intake air temp (deg F): green cool, fading to red by "heat-soaked".
MAT_GREEN_MAX_F = 110.0
MAT_RED_MIN_F = 150.0

RPM_STOPS = (
    (0, COLOR_ALERT_BLUE),
    (RPM_IDLE_MAX, COLOR_ALERT_GREEN),
    (RPM_YELLOW_AT, COLOR_ALERT_YELLOW),
    (RPM_RED_AT, COLOR_ALERT_RED),
)
CLT_STOPS = (
    (CLT_BLUE_MAX_F, COLOR_ALERT_BLUE),
    (CLT_BLUE_MAX_F + CLT_BLEND_BAND_F, COLOR_ALERT_GREEN),
    (CLT_GREEN_MAX_F - CLT_BLEND_BAND_F, COLOR_ALERT_GREEN),
    (CLT_GREEN_MAX_F, COLOR_ALERT_YELLOW),
    (CLT_RED_F, COLOR_ALERT_RED),
)
BOOST_STOPS = (
    (BOOST_BLUE_MAX_PSI, COLOR_ALERT_BLUE),
    (BOOST_MAX_PSI, COLOR_ALERT_RED),
)
MAT_STOPS = (
    (MAT_GREEN_MAX_F, COLOR_ALERT_GREEN),
    (MAT_RED_MIN_F, COLOR_ALERT_RED),
)
AFR_STOPS = (
    (AFR_YELLOW, COLOR_ALERT_GREEN),
    (AFR_RED, COLOR_ALERT_YELLOW),
    (AFR_RED + 1.5, COLOR_ALERT_RED),
)
BATT_STOPS = (
    (BATT_LOW_V, COLOR_ALERT_RED),
    (BATT_COLD_MIN_V, COLOR_ALERT_BLUE),
    (BATT_COLD_MAX_V, COLOR_ALERT_BLUE),
    (BATT_RUN_MIN_V, COLOR_ALERT_GREEN),
    (BATT_RUN_MAX_V, COLOR_ALERT_GREEN),
    (BATT_HIGH_V, COLOR_ALERT_RED),
)
MAP_STOPS = (
    (ATMOSPHERIC_KPA - MAP_BLEND_BAND_KPA, COLOR_ALERT_BLUE),
    (ATMOSPHERIC_KPA + MAP_BLEND_BAND_KPA, COLOR_ALERT_GREEN),
)
# TPS has no danger zone - the gradient is purely visual feedback.
TPS_STOPS = (
    (0, COLOR_ALERT_BLUE),
    (50, COLOR_ALERT_GREEN),
    (100, COLOR_ALERT_YELLOW),
)

# ==== PARAMETERS / PAGES ====================================================
# One (name, units, decimals) entry per single-gauge page, in page order.
# `decimals` is 0 or 1 and controls how the value is formatted everywhere
# (page, overview, beam page, datalog). Names double as CSV column headers.
PAGES = (
    ("RPM", "RPM", 0),
    ("MAP", "kPa", 1),
    ("BOOST", "PSI", 1),
    ("COOLANT", "F", 0),
    ("TPS", "%", 1),
    ("AFR 1", "AFR", 1),
    ("BATTERY", "V", 1),
    ("MAT", "F", 0),
)
NUM_PARAMS = len(PAGES)
(
    PARAM_RPM, PARAM_MAP, PARAM_BOOST, PARAM_CLT, PARAM_TPS, PARAM_AFR,
    PARAM_BATT, PARAM_MAT,
) = range(NUM_PARAMS)

# Two extra pages beyond the 8 single-gauge ones.
OVERVIEW_PAGE = NUM_PARAMS      # 2x2 grid
BEAM_PAGE = NUM_PARAMS + 1      # Boost + MAT dual beam gauges
PAGE_COUNT = NUM_PARAMS + 2

# ==== TIMING (all milliseconds, all > 0) ====================================

# UI render tick. The screen repaints (and display.refresh() runs) at
# 1000/UI_TICK_MS Hz regardless of how fast CAN frames arrive - CAN updates
# state, the render tick consumes it. 50 ms = 20 Hz, plenty for numeric
# gauges while leaving most of the CPU for CAN drain + touch.
UI_TICK_MS = 50

# Touch poll tick. 20 ms = 50 Hz; a deliberate finger tap lasts far longer
# than one period, and polling the TSC2007 over I2C faster just wastes bus
# time.
TOUCH_POLL_MS = 20

# Datalog row interval. 100 ms = 10 Hz, the typical MegaSquirt logging rate;
# faster grows files and SD wear for little tuning benefit.
LOG_INTERVAL_MS = 100

# Boot splash display time, seconds (float). Startup-only - the ONE place
# time.sleep() is allowed, since the main loop hasn't started yet.
SPLASH_DURATION_S = 2.0
SPLASH_IMAGE_PATH = "/splash.bmp"  # 240x320 16-bit RGB565 BMP; silently skipped if absent

# ==== TOUCH (TSC2007 resistive panel) =======================================
# Navigation is tap-zone based, NOT swipe: this resistive panel is too clunky
# for reliable drag gestures (original design decision, kept on purpose).
# Tap left half = previous page, right half = next page, firing on the press
# (finger-down) edge so it feels immediate.

# Raw 12-bit ADC value of the panel's X axis at the physical LEFT and RIGHT
# screen edges, in this portrait rotation. On this panel raw X *decreases*
# left-to-right, hence MIN > MAX - the mapping math handles either order.
# Recalibrate by printing touch.touch values, or simply swap the two numbers
# if left/right feel reversed.
TS_RAW_X_MIN = 3760
TS_RAW_X_MAX = 250

# Minimum TSC2007 pressure (Z1 reading, 0-4095) for a press to count. The
# driver's own touch threshold is 100; raise if vibration false-triggers,
# lower if firm presses are missed.
TOUCH_PRESSURE_THRESHOLD = 100

# Width of the "previous page" zone as a fraction of screen width (0.0-1.0).
# 0.5 splits the screen in half with no dead zone.
TAP_ZONE_FRACTION = 0.5

# ==== DISPLAY / LAYOUT ======================================================
# Portrait orientation: 240 wide x 320 tall.

SCREEN_W = 240
SCREEN_H = 320

# displayio rotation in degrees. This panel mounts landscape-native, so
# portrait needs 90. If your screen comes up upside-down, use 270.
DISPLAY_ROTATION = 90

# SPI clock for the ILI9341, in Hz. 24 MHz is the fastest speed the SAMD51 +
# this panel run reliably at (and displayio's default). Some individual
# panels tolerate 32_000_000 - try it if you want snappier full-screen
# redraws, and drop back if you see visual corruption.
TFT_BAUDRATE = 24_000_000

# TFT control pins - fixed by the FeatherWing's routing; only change these
# if you hand-wired a display to different pins.
TFT_CS_PIN = board.D9
TFT_DC_PIN = board.D10
TFT_RST_PIN = board.D6

# Vertical layout of a single-gauge page (pixel Y centers) and text scales
# (integer multiples of the 6x12 terminalio font).
TITLE_CENTER_Y = 28
TITLE_SCALE = 2
VALUE_CENTER_Y = 140
VALUE_SCALE = 6
UNITS_CENTER_Y = 215
UNITS_SCALE = 2
PEAK_CENTER_Y = 255
PEAK_SCALE = 2
ARROW_SCALE = 3

# Page indicator dots along the bottom.
DOTS_Y = 300
DOT_SIZE = 8
DOT_SPACING = 22

# ==== BARS (per-page graphics above the big value) ==========================

# Round-LED shift-light bar (RPM page): bulb geometry + count.
BAR_Y = 78              # vertical center of every bar, px
BAR_SEGMENT_W = 28      # bulb bounding box (square so circles stay round)
BAR_SEGMENT_H = 28
BAR_GAP = 4
SHIFT_BAR_SEGMENTS = 7
SHIFT_BAR_MAX_RPM = 7000  # bar reads "full" here - set to your redline

# Continuous fill bars (all other single-gauge pages): shared geometry.
INLINE_BAR_W = 210
INLINE_BAR_H = 30
BEAM_BORDER_PX = 2
BEAM_BORDER_COLOR = 0xAAAAAA
BEAM_PEAK_MARKER_COLOR = 0xFFA500  # orange peak / average marker lines
BEAM_PEAK_MARKER_W = 3

# Fill-bar display ranges (real units). Full-scale ends of each bar - the
# color thresholds above are independent of these.
CLT_BAR_MIN_F = 100.0
CLT_BAR_MAX_F = 250.0
MAT_BAR_MIN_F = 60.0
MAT_BAR_MAX_F = 200.0
TPS_BAR_MIN_PCT = 0.0
TPS_BAR_MAX_PCT = 100.0
MAP_BAR_MIN = 0.0
MAP_BAR_MAX = 250.0
BATT_BAR_MIN = 10.0
BATT_BAR_MAX = 16.0
BATT_AVG_MARKER_W = 3   # width of the battery running-average marker line

# AFR center-zero bar: 14.7 (stoich) is pinned to the bar's exact center;
# rich (9-14.7) spans the left half, lean (14.7-18) the right half, each on
# its own linear scale.
AFR_BAR_MIN = 9.0
AFR_BAR_CENTER = 14.7
AFR_BAR_MAX = 18.0
AFR_CENTER_TICK_COLOR = 0xAAAAAA
AFR_CENTER_TICK_W = 2
AFR_PEAK_MARKER_W = 3   # width of the LO / HI hold marker lines

# ==== OVERVIEW GRID PAGE ====================================================
# Which four channels appear in the 2x2 grid, and where each cell's name /
# value sit (pixel centers). Order: top-left, top-right, bottom-left,
# bottom-right.
GRID_PARAMS = (PARAM_BOOST, PARAM_CLT, PARAM_AFR, PARAM_BATT)
GRID_NAME_SCALE = 2
GRID_VALUE_SCALE = 3
GRID_NAME_POS = ((60, 55), (180, 55), (60, 195), (180, 195))
GRID_VALUE_POS = ((60, 105), (180, 105), (60, 245), (180, 245))

# ==== BEAM GAUGE PAGE (Boost + MAT) =========================================
BEAM_BAR_W = 210
BEAM_BAR_H = 45
BEAM_LABEL_SCALE = 2
BEAM_VALUE_SCALE = 4
BEAM_TOP_TEXT_Y = 45    # Boost label/value row center
BEAM_TOP_BAR_Y = 75     # Boost bar top edge
BEAM_BOT_TEXT_Y = 195   # MAT label/value row center
BEAM_BOT_BAR_Y = 225    # MAT bar top edge

# ==== DATALOGGER (SD card) ==================================================

# Chip-select for the FeatherWing's onboard microSD slot. Fixed by the wing.
SD_CS_PIN = board.D5

# Directory on the card for log files (created if missing). Files are
# numbered log001.csv, log002.csv, ... one per engine-running session.
LOG_DIR = "/sd/logs"

# RPM above which the engine counts as "running": a log session starts when
# RPM rises past this and stops when it falls back below. 300 sits safely
# between cranking and the lowest realistic idle.
ENGINE_RUNNING_RPM = 300
