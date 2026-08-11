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

# True = open the CAN controller in SILENT mode: the transmit pin is held
# high and the dash cannot put anything on the bus at all, not even an
# acknowledge. False (default) = a normal bus participant.
#
# Read this before changing it, because neither setting is safe in every
# install. A normal node ACKs the frames it receives and signals errors it
# detects - so a dash with the WRONG BAUD RATE doesn't just show dashes, it
# actively disturbs the bus, and auto_restart means it keeps doing so. If your
# bus carries traffic the engine depends on (an MS3 expansion board, a CAN
# wideband, a CAN-connected ignition or transmission controller), silent mode
# removes that risk entirely.
#
# But on the two-node bus this project describes - one MegaSquirt, one dash -
# the dash is the ECU's ONLY source of acknowledges. Go silent there and the
# ECU sees an unacknowledged bus and eventually goes error-passive itself.
# So: leave False for a plain ECU-plus-dash bus, consider True only when
# other nodes are present to do the acknowledging.
CAN_SILENT_MODE = False

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

# (The per-channel plausibility gate, and which channels keep a running
# average or minimum, live in the PARAMETERS section below - they are indexed
# by PARAM_*, which isn't defined until then.)

# ==== DERIVED CHANNELS ======================================================

# Barometric reference for the Boost calculation: boost PSI =
# (MAP - baro) / KPA_PER_PSI.
#
# By default (OVERRIDE = None) the dash measures baro ITSELF: with the
# engine off, MAP *is* local barometric pressure, read by the very sensor
# boost is derived from (so sensor offset error cancels in the subtraction).
# The reference locks from the first CAN frames where RPM == 0, keeps
# slowly tracking whenever the engine is off (so a mountain-pass fuel stop
# recalibrates it), and freezes while the engine runs. Works out of the box
# at any altitude, in any weather - the same "initial MAP reading" strategy
# MegaSquirt itself uses for baro correction.
#
# Set a number (kPa, e.g. 101.3) to pin the reference instead - for bench
# determinism or unusual setups. Your correct pinned value is simply the
# MAP your dash shows with the engine off.
ATMOSPHERIC_KPA_OVERRIDE = None

# Used as the reference only until the first valid engine-off capture (or
# forever, if the dash never sees RPM == 0 - e.g. powered up mid-drive).
# Sea-level standard atmosphere.
BARO_FALLBACK_KPA = 101.3

# Plausibility window for baro capture, kPa. Rejects garbage frames and -
# together with the RPM gate - can never mistake idle vacuum for
# atmosphere. 55-110 covers roughly -1,000 ft to 15,000 ft elevation.
BARO_MIN_KPA = 55.0
BARO_MAX_KPA = 110.0

# Engine-off tracking low-pass strength: each qualifying frame moves the
# reference by (MAP - ref) / 2**BARO_FILTER_SHIFT. 3 = 1/8 per frame, i.e.
# settles a step change in a couple of seconds at the ~18 Hz frame rate
# while shrugging off single-frame noise.
BARO_FILTER_SHIFT = 3

# How many CONSECUTIVE engine-off frames must agree before the reference
# first locks, and how far apart (kPa) they may be and still count as
# agreeing.
#
# Why this exists: the plausibility window above is 55 kPa wide, so a single
# corrupt-but-in-range frame from a still-booting ECU used to be accepted as
# gospel - and because the reference then FREEZES the moment the engine
# starts, that one frame could put a fixed offset on every boost reading and
# every logged boost row for the whole drive, with nothing on screen to say
# so. Requiring several frames to agree makes that essentially impossible;
# an ECU emitting garbage does not emit the SAME garbage eight times running.
#
# 8 samples is ~0.45 s at the ~18 Hz broadcast rate - far less than the time
# an ECU takes to boot and prime before you can crank it, so in practice the
# lock still happens before the engine turns over. Raise for more paranoia,
# lower if your ECU broadcasts only briefly before you start.
BARO_LOCK_SAMPLES = 8
BARO_LOCK_SPREAD_KPA = 0.5

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
# straddling atmospheric so it doesn't flicker at the boundary. The
# boundary follows the live baro reference (see barometric section above),
# so MAP's color stops are built at runtime in ui.py, not in the static
# tables below.
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
# (MAP has no static stop table - ui.py rebuilds its blue/green stops
# around the live baro reference whenever that reference moves.)
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

# --- Plausibility gate at the trust boundary -------------------------------
# (min, max) in each channel's real display units, in PARAM_* order. A decoded
# sample outside its range is DROPPED - not clamped, because a clamped value
# is a plausible-looking lie while a dropped one shows "---" and tells the
# truth.
#
# Why: CAN carries no authentication and the dash believes any frame bearing
# the right ID. One corrupt frame that passes CRC used to flow straight into
# the display, into PEAK/LO (which persist until power-off), and into the CSV
# log - so a single glitch could leave "PEAK 6553.5" on the MAP page for the
# rest of the drive with no way to clear it. These bounds are deliberately
# WIDE: the job is rejecting the impossible, not second-guessing your engine.
#
# The AFR floor earns its keep twice over: a cold or unplugged wideband
# reports 0.0, and because LO tracks the lowest AFR ever seen, that used to
# pin "LO 0.0" for the whole session and make the readout decorative.
SANE_RANGES = (
    (0.0, 20000.0),     # RPM
    (0.0, 400.0),       # MAP kPa (4 bar absolute)
    (-20.0, 60.0),      # BOOST PSI (derived; negative is vacuum)
    (-60.0, 350.0),     # COOLANT F
    (-10.0, 110.0),     # TPS %
    (5.0, 25.0),        # AFR (wire byte gives 0.0-25.5; 0 = sensor not live)
    (0.0, 20.0),        # BATTERY V
    (-60.0, 300.0),     # MAT F
)

# Which channels keep a running average / a running minimum. PEAK is kept for
# every channel (every single-gauge page shows it); these two are read by
# exactly one page each, and accumulating them for all eight was pure cost -
# the RPM accumulator in particular outgrows CircuitPython's 31-bit small
# integers within ~17 minutes of driving and starts allocating on the decode
# path, in a design that is otherwise allocation-free in steady state.
AVG_PARAMS = (PARAM_BATT,)   # Battery's "AVG" readout
LOW_PARAMS = (PARAM_AFR,)    # AFR's "LO" readout

# ==== ALARMS ================================================================
# (low, high) alarm limits per channel in real units, in PARAM_* order; None
# on either side means "no limit that way". Crossing a limit raises a banner
# naming the channel and its value on EVERY page, and marks the value with a
# trailing "!".
#
# Why this exists: the dash has ten pages and shows one at a time, so a
# coolant temperature climbing into the red on page 4 was invisible from page
# 1 - the single condition this thing exists to catch, on the channel where
# catching it late costs an engine. Alert state was also carried by color
# alone, which is no use to the ~8% of men with red/green color deficiency or
# to anyone reading the panel in direct sun.
#
# The limits reference the SAME constants as the color tables above, so a
# gauge's "red" and its alarm can never drift apart.
ALARMS = (
    (None, None),               # RPM - see note below
    (None, None),               # MAP - boost is the one that matters
    (None, BOOST_MAX_PSI),      # BOOST - over your ceiling
    (None, CLT_RED_F),          # COOLANT - overheating
    (None, None),               # TPS - no danger zone
    (None, AFR_RED + 1.5),      # AFR - dangerously lean (the fully-red stop)
    (BATT_LOW_V, BATT_HIGH_V),  # BATTERY - flat or overcharging
    (None, MAT_RED_MIN_F),      # MAT - heat-soaked
)

# RPM is deliberately NOT alarmed: the shift-light bar already reports it, and
# anyone using this on a car they rev to redline on purpose would get a
# banner on every gear change. An alarm that cries wolf gets ignored, which
# costs you the ones that matter.

# Only alarm while the engine is actually running (RPM above
# ENGINE_RUNNING_RPM). Leave True: a cold wideband, a cranking voltage dip,
# and an engine-off battery reading are all "alarming" values that mean
# nothing out of context, and a dash that shouts at key-on teaches you to
# ignore it.
ALARM_ONLY_WHEN_RUNNING = True

# Which channel wins when several are in alarm at once - most consequential
# first. Only channels with a limit above need appear here.
ALARM_PRIORITY = (PARAM_CLT, PARAM_BATT, PARAM_BOOST, PARAM_MAT, PARAM_AFR)

# Alarm banner blink period, ms (> 0). The banner alternates on/off at this
# rate so it catches peripheral vision; the "!" on the value never blinks, so
# the alarm is still legible in a still photograph.
ALARM_BLINK_MS = 500

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

# ==== FAULT CONTAINMENT =====================================================
# The main loop catches exceptions instead of dying: a fault in ANY subsystem
# (a yanked SD card, a glitching touch chip) must never take the display down
# with it. These control what happens after a catch.

# Faults tolerated before the loop gives up and shows the red fatal screen.
# The counter clears itself after FAULT_CLEAR_MS quiet, so this really means
# "this many faults inside one rolling window" - a lone glitch every few
# minutes keeps the dash alive; something failing continuously halts it.
MAX_FAULTS_BEFORE_HALT = 20

# Quiet period (ms, > 0) after which the fault counter resets and the on-screen
# FLT indicator clears.
FAULT_CLEAR_MS = 5000

# Hardware watchdog timeout in SECONDS (float). The loop feeds it every pass;
# if the loop ever hangs (rather than raising) the chip resets and the dash
# comes back on its own. 0 = disabled.
#
# Left DISABLED by default on purpose, for two reasons worth knowing before you
# turn it on: (1) not every CircuitPython port exposes microcontroller.watchdog
# - if yours doesn't, code.py just reports that over serial and carries on;
# (2) in RESET mode the timer keeps running after your code stops, so a board
# sitting at the REPL for development reboots every few seconds. Set this to
# ~4.0 for a dash that lives in a car (comfortably above the worst measured
# loop time - see PERFORMANCE.md), and back to 0 while developing on the bench.
WATCHDOG_TIMEOUT_S = 0.0

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

# Hold this long (ms, > 0) to reset every PEAK / LO / AVG readout - the only
# way to clear them short of a power cycle, which also ends your datalog
# session. The page-change the press already fired is undone, so a hold leaves
# you where you were. Confirmation is the readouts themselves snapping to
# "PEAK ---".
TOUCH_HOLD_MS = 1500

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

# Barometric-reference readout, shown on the Boost page only (small, below
# the peak line). Boost is MAP minus this reference, so when the reference is
# a guess rather than a measurement the number above it is a guess too - the
# readout says which.
BARO_CENTER_Y = 278

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


# ==== VALIDATION ============================================================
# Called once by code.py before anything is constructed. This file is the one
# users are invited to edit, and several plausible edits used to fail LATER,
# somewhere else, with a traceback that never names the setting: swapping
# TS_RAW_X_MIN/MAX to the same value divided by zero on the first screen tap;
# a bar whose min equals its max divided by zero the first time you navigated
# to that page. Fail at startup instead, naming the setting.

def validate():
    """Check the settings above for values that would crash or mislead later.

    Returns None when everything is sane, else a SHORT message naming the
    first bad setting - short because it goes on the fatal screen, which fits
    about 20 characters. The full explanation for every problem found is
    printed to the USB serial console.
    """
    problems = []

    def _check(ok, name, detail):
        if not ok:
            problems.append((name, detail))

    # --- things that divide, and therefore must never be zero -------------
    _check(TS_RAW_X_MIN != TS_RAW_X_MAX, "TS_RAW_X",
           "TS_RAW_X_MIN and TS_RAW_X_MAX must differ (swap them to reverse "
           "left/right, don't equalize them)")
    _check(SHIFT_BAR_MAX_RPM > 0, "SHIFT_BAR_RPM", "SHIFT_BAR_MAX_RPM must be > 0")
    _check(SHIFT_BAR_SEGMENTS > 0, "SHIFT_SEGMENTS", "SHIFT_BAR_SEGMENTS must be > 0")
    _check(KPA_PER_PSI > 0, "KPA_PER_PSI", "KPA_PER_PSI must be > 0")
    for name, lo, hi in (
        ("CLT_BAR", CLT_BAR_MIN_F, CLT_BAR_MAX_F),
        ("MAT_BAR", MAT_BAR_MIN_F, MAT_BAR_MAX_F),
        ("TPS_BAR", TPS_BAR_MIN_PCT, TPS_BAR_MAX_PCT),
        ("MAP_BAR", MAP_BAR_MIN, MAP_BAR_MAX),
        ("BATT_BAR", BATT_BAR_MIN, BATT_BAR_MAX),
        ("BOOST_BAR", BOOST_MIN_PSI, BOOST_MAX_PSI),
    ):
        _check(hi > lo, name, "{} max must be greater than min".format(name))
    _check(AFR_BAR_MIN < AFR_BAR_CENTER < AFR_BAR_MAX, "AFR_BAR",
           "AFR_BAR_MIN < AFR_BAR_CENTER < AFR_BAR_MAX must hold")

    # --- CAN ---------------------------------------------------------------
    _check(0 <= BASE_CAN_ID <= 2044, "BASE_CAN_ID",
           "BASE_CAN_ID must be 0-2044 (base+3 has to stay a valid 11-bit ID)")
    _check(CAN_BAUD_RATE > 0, "CAN_BAUD_RATE", "CAN_BAUD_RATE must be > 0")
    _check(CAN_MAX_FRAMES_PER_UPDATE > 0, "CAN_MAX_FRAMES",
           "CAN_MAX_FRAMES_PER_UPDATE must be > 0 or no frame is ever decoded")
    for name, ofs, width in (
        ("OFS_MAP", OFS_MAP, 2), ("OFS_RPM", OFS_RPM, 2), ("OFS_CLT", OFS_CLT, 2),
        ("OFS_TPS", OFS_TPS, 2), ("OFS_MAT", OFS_MAT, 2), ("OFS_BATT", OFS_BATT, 2),
        ("OFS_AFR1", OFS_AFR1, 1),
    ):
        _check(0 <= ofs <= 8 - width, name,
               "{} = {} doesn't fit inside an 8-byte frame".format(name, ofs))

    # --- barometric --------------------------------------------------------
    _check(BARO_MIN_KPA < BARO_MAX_KPA, "BARO_RANGE",
           "BARO_MIN_KPA must be below BARO_MAX_KPA")
    _check(BARO_FILTER_SHIFT >= 0, "BARO_SHIFT", "BARO_FILTER_SHIFT must be >= 0")
    _check(BARO_LOCK_SAMPLES >= 1, "BARO_SAMPLES", "BARO_LOCK_SAMPLES must be >= 1")
    _check(BARO_LOCK_SPREAD_KPA >= 0, "BARO_SPREAD", "BARO_LOCK_SPREAD_KPA must be >= 0")
    _check(ATMOSPHERIC_KPA_OVERRIDE is None or ATMOSPHERIC_KPA_OVERRIDE > 0,
           "BARO_OVERRIDE", "ATMOSPHERIC_KPA_OVERRIDE must be None or a positive kPa")

    # --- timing (all milliseconds, all > 0) --------------------------------
    for name, value in (
        ("STALE_TIMEOUT", STALE_TIMEOUT_MS), ("UI_TICK_MS", UI_TICK_MS),
        ("TOUCH_POLL_MS", TOUCH_POLL_MS), ("LOG_INTERVAL_MS", LOG_INTERVAL_MS),
        ("DEBUG_INTERVAL", DEBUG_INTERVAL_MS), ("FAULT_CLEAR_MS", FAULT_CLEAR_MS),
    ):
        _check(value > 0, name, "{} must be > 0 ms".format(name))
    _check(MAX_FAULTS_BEFORE_HALT >= 1, "MAX_FAULTS", "MAX_FAULTS_BEFORE_HALT must be >= 1")
    _check(WATCHDOG_TIMEOUT_S >= 0, "WATCHDOG", "WATCHDOG_TIMEOUT_S must be >= 0 (0 = off)")
    _check(SPLASH_DURATION_S >= 0, "SPLASH", "SPLASH_DURATION_S must be >= 0")

    # --- touch / display / pages -------------------------------------------
    _check(0.0 < TAP_ZONE_FRACTION < 1.0, "TAP_ZONE",
           "TAP_ZONE_FRACTION must be between 0 and 1 exclusive, or one tap "
           "zone becomes unreachable")
    _check(DISPLAY_ROTATION in (0, 90, 180, 270), "ROTATION",
           "DISPLAY_ROTATION must be 0, 90, 180 or 270")
    _check(SCREEN_W > 0 and SCREEN_H > 0, "SCREEN", "SCREEN_W/H must be > 0")
    _check(len(PAGES) == NUM_PARAMS, "PAGES", "NUM_PARAMS must match len(PAGES)")
    for i, entry in enumerate(PAGES):
        _check(len(entry) == 3 and entry[2] in (0, 1), "PAGES",
               "PAGES[{}] must be (name, units, decimals) with decimals 0 or 1".format(i))
    _check(len(GRID_PARAMS) == len(GRID_NAME_POS) == len(GRID_VALUE_POS), "GRID",
           "GRID_PARAMS, GRID_NAME_POS and GRID_VALUE_POS must be the same length")

    # --- plausibility gate and alarms --------------------------------------
    # A short SANE_RANGES would index out of bounds on the decode path, i.e.
    # on the first CAN frame - exactly the kind of late, confusing failure
    # this function exists to pull forward to startup.
    _check(len(SANE_RANGES) == NUM_PARAMS, "SANE_RANGES",
           "SANE_RANGES needs one (min, max) entry per channel")
    if len(SANE_RANGES) == NUM_PARAMS:
        for p, (lo, hi) in enumerate(SANE_RANGES):
            _check(lo < hi, "SANE_RANGES",
                   "SANE_RANGES[{}] ({}) must have min < max".format(p, PAGES[p][0]))
    _check(len(ALARMS) == NUM_PARAMS, "ALARMS",
           "ALARMS needs one (low, high) entry per channel")
    if len(ALARMS) == NUM_PARAMS and len(SANE_RANGES) == NUM_PARAMS:
        for p, (lo, hi) in enumerate(ALARMS):
            sane_lo, sane_hi = SANE_RANGES[p]
            # A limit outside the plausibility gate can never fire: the
            # sample would be dropped before anything could compare it.
            _check(lo is None or sane_lo < lo < sane_hi, "ALARMS",
                   "ALARMS[{}] low limit is outside SANE_RANGES[{}]".format(p, p))
            _check(hi is None or sane_lo < hi < sane_hi, "ALARMS",
                   "ALARMS[{}] high limit is outside SANE_RANGES[{}]".format(p, p))
            _check(lo is None or hi is None or lo < hi, "ALARMS",
                   "ALARMS[{}] low limit must be below its high limit".format(p))
            if lo is not None or hi is not None:
                _check(p in ALARM_PRIORITY, "ALARM_PRIORITY",
                       "{} has alarm limits but isn't in ALARM_PRIORITY, so it "
                       "can never raise one".format(PAGES[p][0]))
    for p in ALARM_PRIORITY:
        _check(0 <= p < NUM_PARAMS, "ALARM_PRIORITY",
               "ALARM_PRIORITY entry {} is not a valid PARAM_* index".format(p))
    _check(ALARM_BLINK_MS > 0, "ALARM_BLINK", "ALARM_BLINK_MS must be > 0 ms")
    for p in AVG_PARAMS:
        _check(0 <= p < NUM_PARAMS, "AVG_PARAMS", "bad PARAM_* index in AVG_PARAMS")
    for p in LOW_PARAMS:
        _check(0 <= p < NUM_PARAMS, "LOW_PARAMS", "bad PARAM_* index in LOW_PARAMS")
    _check(TOUCH_HOLD_MS > 0, "TOUCH_HOLD_MS", "TOUCH_HOLD_MS must be > 0 ms")
    for p in GRID_PARAMS:
        _check(0 <= p < NUM_PARAMS, "GRID_PARAMS",
               "GRID_PARAMS entry {} is not a valid PARAM_* index".format(p))
    _check(ENGINE_RUNNING_RPM >= 0, "ENGINE_RPM", "ENGINE_RUNNING_RPM must be >= 0")

    if not problems:
        return None
    print("--- config.py problems ---")
    for name, detail in problems:
        print("  {}: {}".format(name, detail))
    return "CFG: " + problems[0][0]
