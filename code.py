# MegaSquirt (Microsquirt MS2/Extra) CAN Digital Dash - CircuitPython
# ---------------------------------------------------------------------
# https://github.com/<you>/megasquirt-can-dash  (see README.md for full setup)
#
# Hardware:
#   - Adafruit Feather M4 CAN Express (native CAN peripheral, board.CAN_TX/CAN_RX)
#   - Adafruit 2.4" TFT FeatherWing V2 (ILI9341 + TSC2007 resistive touch, I2C)
#     - onboard microSD slot used for datalogging (CS on board.D5)
#   - MegaSquirt Microsquirt (MS2/Extra) ECU with "Simplified Dash Broadcasting" enabled
#
# Pages (tap the left/right half of the screen to move between them, wraps
# around, dots at the bottom show which page you're on):
#   1-8. Single-gauge pages: RPM, MAP, Boost, Coolant, TPS, AFR1, Battery, MAT
#        - big color-coded number, units, peak-hold ("PEAK ###")
#        - RPM: race-style light bar (round LED bulbs, smooth color gradient)
#          above the value
#        - MAP: continuous fill bar (0-250) above the value; both the bar and
#          the big number are colored blue under vacuum (below atmospheric
#          pressure) or green under boost (above atmospheric)
#        - Boost: continuous fill bar (0-21 PSI) above the value, flat blue
#          up through 18 PSI then fading to red by 21 PSI
#        - Coolant: continuous fill bar above the value, blue (<160F) / green
#          (160-200F) / yellow fading to red (200-230F)
#        - TPS: continuous fill bar (0-100%) above the value, blue->green->
#          yellow gradient as the throttle opens
#        - MAT: continuous fill bar above the value, green fading to red as
#          intake air temp climbs
#        - AFR1: continuous bar above the value, but centered on 14.7
#          (stoich) instead of filling from the left - the fill grows from the
#          center outward, left into the rich side (9-14.7) or right into the
#          lean side (14.7-18), tracking the current AFR. Two orange markers
#          hold the richest (low) and leanest (high) AFR reached, shown below
#          as "LO ##.# HI ##.#"
#        - Battery: continuous fill bar (10-16V) above the value, colored by
#          the same gradient as the number - blue (normal, engine off,
#          12.2-12.6V) -> green (normal, engine running/charging,
#          13-14.7V) -> red (low below ~11.5V or high above ~15V). Its marker
#          and readout are a running average ("AVG ##.#"), not a peak, since
#          average system voltage tells you more about a battery's health.
#   9.   Overview grid: Boost/Coolant/AFR/Battery, 2x2, one glance
#   10.  Beam gauges: Boost + MAT as two continuous VU-meter-style bars, each
#        with an orange peak-hold marker
#
# Other behavior:
#   - Stale data (no CAN frame for >1s) shows "---" and a "NO CAN" banner
#   - Top-left corner always shows the datalogger state: NO SD / LOG OFF / LOG ON
#   - Optional boot splash (see SPLASH_IMAGE_PATH below) shown for 2s at power-up
#   - Non-blocking main loop throughout - no delay() equivalents once running
#
# Datalogging note: this writes plain CSV, not a real MegaLogViewer .mlg
# binary file. MLG is a proprietary binary format (EFI Analytics' own
# MLVLG spec) that's hard to guarantee byte-perfect from a fresh
# microcontroller implementation with no way to test against the real
# closed-source parser - a subtly wrong header would just produce a file
# MegaLogViewer refuses to open. MegaLogViewer HD's own delimited-file loader
# explicitly supports CSV with a name row + a units row (which is what this
# writes), so it opens directly with no conversion step. Logging only runs
# while there's a physical SD card in the FeatherWing's slot; see the
# "Datalogger" section below.
#
# CircuitPython libraries needed on CIRCUITPY/lib (see README.md for the
# exact bundle version this was built/tested against):
#   adafruit_display_text, adafruit_ili9341, adafruit_tsc2007, adafruit_sdcard
#   (adafruit_bus_device is a dependency of the above, also needed)
# (canio, displayio, terminalio, digitalio, os, storage, bitmaptools are
# built into CircuitPython itself - nothing to install for those.)

import time
import os
import board
import digitalio
import displayio
import fourwire
import terminalio
import storage
import bitmaptools
from adafruit_display_text import label
import adafruit_ili9341
import adafruit_tsc2007
import adafruit_sdcard
import canio

# ============================================================================
#  CAN protocol: MegaSquirt MS2/Extra "Simplified Dash Broadcasting"
# ============================================================================
# All multi-byte fields are big-endian (Motorola byte order, MSB first),
# packed as 16-bit words into 8-byte standard-ID (11-bit) CAN frames, starting
# at a base ID that is configurable in TunerStudio (CAN/GPIO/Testmodes ->
# "CAN ID (Base)"). Change BASE_CAN_ID below to match your TunerStudio setting.

CAN_BAUD_RATE = 500_000  # 500 kbps

BASE_CAN_ID = 1512  # 0x5E8 default - must match TunerStudio setting

# Confirmed against the official "Megasquirt CAN realtime data broadcast
# protocol" doc (2016-02-17, EFI Analytics/msextra.com), field list table.
CAN_ID_DASH0 = BASE_CAN_ID + 0  # map, rpm, clt, tps
CAN_ID_DASH1 = BASE_CAN_ID + 1  # pw1, pw2, mat, adv_deg
CAN_ID_DASH2 = BASE_CAN_ID + 2  # afrtgt1, AFR1, EGOcor1, egt1, pwseq1
CAN_ID_DASH3 = BASE_CAN_ID + 3  # batt, sensors1

# --- Byte offsets within CAN_ID_DASH0 (8 data bytes = four big-endian 16-bit words) ---
OFS_MAP = 0  # bytes 0-1: MAP, int16,  kPa * 10
OFS_RPM = 2  # bytes 2-3: RPM, uint16, rpm  (x1, not scaled)
OFS_CLT = 4  # bytes 4-5: CLT, int16,  deg F * 10
OFS_TPS = 6  # bytes 6-7: TPS, int16,  % * 10

# --- Byte offset of MAT (Manifold Air Temperature) within CAN_ID_DASH1 ---
# Bytes 0-1/2-3 of this frame are pw1/pw2 (unused here) - mat is the third
# word, at offset 4.
OFS_MAT = 4  # bytes 4-5: MAT, int16, deg F * 10

# --- Byte offset of AFR1 within CAN_ID_DASH2 ---
# Byte 0 of this frame is afrtgt1 (AFR target, unused here) - AFR1 itself is
# a single unsigned byte at offset 1, NOT a 2-byte word at offset 0 (that was
# wrong in an earlier version of this file - it was actually reading
# afrtgt1+AFR1 combined as one bogus 16-bit value).
OFS_AFR1 = 1  # byte 1 only: AFR1, uint8, AFR * 10

# --- Byte offset of battery voltage within CAN_ID_DASH3 ---
OFS_BATT = 0  # bytes 0-1: battery voltage, int16, Volts * 10

# --- Scale factors: real_value = raw_int / SCALE ---
SCALE_MAP = 10.0
SCALE_RPM = 1.0
SCALE_CLT = 10.0
SCALE_TPS = 10.0
SCALE_MAT = 10.0
SCALE_AFR1 = 10.0
SCALE_BATT = 10.0

# How long (seconds) a parameter's last CAN update may age before it's stale.
STALE_TIMEOUT_S = 1.0

# ============================================================================
#  Derived channels (not on the CAN bus - computed from other live channels)
# ============================================================================
# Boost gauge pressure (PSI): MAP is an absolute manifold pressure reading in
# kPa. "Boost" is that pressure relative to atmospheric, in PSI - positive
# under boost, negative under vacuum, reading ~0 at idle/key-on with the
# engine not running. Adjust ATMOSPHERIC_KPA for your altitude/weather if you
# want it to read exactly 0 at idle.
ATMOSPHERIC_KPA = 101.3
KPA_PER_PSI = 6.894757

# ============================================================================
#  Color-coded alert thresholds - "instant readability at a glance"
# ============================================================================
# Each channel below gets green/yellow/red coloring on its gauge page (and on
# the overview grid). Channels not listed here (MAP, TPS) just show in plain
# white - add a threshold set following the same pattern if you want those
# colored too.

COLOR_ALERT_GREEN = 0x00FF00
COLOR_ALERT_YELLOW = 0xFFFF00
COLOR_ALERT_RED = 0xFF0000
COLOR_ALERT_BLUE = 0x4080FF


def lerp_color(color_a, color_b, frac):
    # Blends two 0xRRGGBB colors.
    if frac < 0:
        frac = 0
    elif frac > 1:
        frac = 1
    ar, ag, ab = (color_a >> 16) & 0xFF, (color_a >> 8) & 0xFF, color_a & 0xFF
    br, bg, bb = (color_b >> 16) & 0xFF, (color_b >> 8) & 0xFF, color_b & 0xFF
    r = int(ar + (br - ar) * frac)
    g = int(ag + (bg - ag) * frac)
    b = int(ab + (bb - ab) * frac)
    return (r << 16) | (g << 8) | b


def gradient_color(value, stops):
    # stops: a list of (value, color) pairs, sorted ascending by value.
    # Below the first stop -> that stop's flat color. Above the last -> that
    # stop's flat color. In between -> smoothly blended between the two
    # nearest stops. Repeating the same color at two adjacent stops creates a
    # flat "plateau" in the middle of an otherwise-gradient scale (used below
    # for coolant temp's safe zone).
    if value <= stops[0][0]:
        return stops[0][1]
    for i in range(1, len(stops)):
        v0, c0 = stops[i - 1]
        v1, c1 = stops[i]
        if value <= v1:
            frac = (value - v0) / (v1 - v0)
            return lerp_color(c0, c1, frac)
    return stops[-1][1]


# Coolant temperature (deg F):
#   - blue at/below CLT_BLUE_MAX_F (160): not warmed up yet
#   - green through the normal operating range up to CLT_GREEN_MAX_F (200)
#   - yellow at CLT_GREEN_MAX_F fading to red by CLT_RED_F (230): overheating
# The blue->green and green->yellow edges blend over CLT_BLEND_BAND_F rather
# than snapping instantly.
CLT_BLUE_MAX_F = 160.0
CLT_GREEN_MAX_F = 200.0
CLT_RED_F = 230.0
CLT_BLEND_BAND_F = 10.0

# AFR: green at/below AFR_YELLOW (at or richer than that), yellow up to
# AFR_RED, red beyond - i.e. "leaner than 15.5 under load" reads red. This is
# a simple static threshold, not actually load-gated (no separate check
# against TPS/MAP) - adjust AFR_RED if your target AFR under boost differs.
AFR_YELLOW = 14.7
AFR_RED = 15.5

# Battery voltage: a gradient with two distinct "normal" plateaus rather than
# a single safe zone, since a healthy battery reads differently depending on
# whether the engine is running:
#   - BATT_LOW_V and below: red (weak/dying battery)
#   - BATT_COLD_MIN_V - BATT_COLD_MAX_V: blue ("cold" - normal resting voltage
#     with the engine off)
#   - BATT_RUN_MIN_V - BATT_RUN_MAX_V: green (normal alternator/charging
#     voltage with the engine running)
#   - BATT_HIGH_V and above: red ("hot" - overcharging/regulator fault)
# The gaps between plateaus (12.6-13.0 and 14.7-15.0) blend smoothly.
BATT_LOW_V = 11.5
BATT_COLD_MIN_V = 12.2
BATT_COLD_MAX_V = 12.6
BATT_RUN_MIN_V = 13.0
BATT_RUN_MAX_V = 14.7
BATT_HIGH_V = 15.0

# MAP (kPa): blue under vacuum (below atmospheric - throttle closed/idle),
# green under boost (above atmospheric). Blends smoothly across a narrow band
# straddling ATMOSPHERIC_KPA rather than snapping instantly at that value.
MAP_BLEND_BAND_KPA = 5.0

# RPM: matches the shift-bar thresholds below, so the big number and the bar
# always agree. Unlike the old flat blue/green/yellow/red bands, this is now
# a continuous gradient across the whole range (motorsport shift-light
# style) - blue only right at idle, then a smooth green->yellow->red climb.
RPM_IDLE_MAX = 1000  # blue up to here
RPM_YELLOW_AT = 5300  # pure yellow here
RPM_RED_AT = 6100  # pure red here and beyond

# Boost (PSI): flat blue from idle all the way up through BOOST_BLUE_MAX_PSI
# (covers everything up to a typical boost target with no color change), then
# fading to red as it climbs the rest of the way to BOOST_MAX_PSI.
BOOST_MIN_PSI = 0.0
BOOST_BLUE_MAX_PSI = 18.0
BOOST_MAX_PSI = 21.0

# MAT (Manifold Air Temp, deg F): green at/below MAT_GREEN_MAX_F, gradually
# fading to red by MAT_RED_MIN_F and beyond.
MAT_GREEN_MAX_F = 110.0
MAT_RED_MIN_F = 150.0

RPM_STOPS = [
    (0, COLOR_ALERT_BLUE),
    (RPM_IDLE_MAX, COLOR_ALERT_GREEN),
    (RPM_YELLOW_AT, COLOR_ALERT_YELLOW),
    (RPM_RED_AT, COLOR_ALERT_RED),
]

CLT_STOPS = [
    (CLT_BLUE_MAX_F, COLOR_ALERT_BLUE),
    (CLT_BLUE_MAX_F + CLT_BLEND_BAND_F, COLOR_ALERT_GREEN),
    (CLT_GREEN_MAX_F - CLT_BLEND_BAND_F, COLOR_ALERT_GREEN),
    (CLT_GREEN_MAX_F, COLOR_ALERT_YELLOW),
    (CLT_RED_F, COLOR_ALERT_RED),
]

BOOST_STOPS = [
    (BOOST_BLUE_MAX_PSI, COLOR_ALERT_BLUE),
    (BOOST_MAX_PSI, COLOR_ALERT_RED),
]

MAT_STOPS = [
    (MAT_GREEN_MAX_F, COLOR_ALERT_GREEN),
    (MAT_RED_MIN_F, COLOR_ALERT_RED),
]

AFR_STOPS = [
    (AFR_YELLOW, COLOR_ALERT_GREEN),
    (AFR_RED, COLOR_ALERT_YELLOW),
    (AFR_RED + 1.5, COLOR_ALERT_RED),
]

BATT_STOPS = [
    (BATT_LOW_V, COLOR_ALERT_RED),
    (BATT_COLD_MIN_V, COLOR_ALERT_BLUE),
    (BATT_COLD_MAX_V, COLOR_ALERT_BLUE),
    (BATT_RUN_MIN_V, COLOR_ALERT_GREEN),
    (BATT_RUN_MAX_V, COLOR_ALERT_GREEN),
    (BATT_HIGH_V, COLOR_ALERT_RED),
]

MAP_STOPS = [
    (ATMOSPHERIC_KPA - MAP_BLEND_BAND_KPA, COLOR_ALERT_BLUE),
    (ATMOSPHERIC_KPA + MAP_BLEND_BAND_KPA, COLOR_ALERT_GREEN),
]

# TPS (%): no real "danger zone", but it still gets a gradient purely for
# visual feedback as the throttle opens, rather than a flat single color.
TPS_STOPS = [
    (0, COLOR_ALERT_BLUE),
    (50, COLOR_ALERT_GREEN),
    (100, COLOR_ALERT_YELLOW),
]


def rpm_alert_color(rpm):
    return gradient_color(rpm, RPM_STOPS)


def clt_alert_color(temp_f):
    return gradient_color(temp_f, CLT_STOPS)


def boost_alert_color(psi):
    return gradient_color(psi, BOOST_STOPS)


def mat_alert_color(temp_f):
    return gradient_color(temp_f, MAT_STOPS)


def afr_alert_color(afr):
    return gradient_color(afr, AFR_STOPS)


def battery_alert_color(volts):
    return gradient_color(volts, BATT_STOPS)


def map_alert_color(kpa):
    return gradient_color(kpa, MAP_STOPS)


def tps_alert_color(pct):
    return gradient_color(pct, TPS_STOPS)


def get_value_color(param, value):
    # PARAM_* constants aren't defined until the "Parameters / pages" section
    # below runs, but this function is only ever called after that (at
    # runtime, from update_display()/update_overview()), so it's fine.
    if param == PARAM_CLT:
        return clt_alert_color(value)
    if param == PARAM_AFR:
        return afr_alert_color(value)
    if param == PARAM_BATT:
        return battery_alert_color(value)
    if param == PARAM_MAP:
        return map_alert_color(value)
    if param == PARAM_RPM:
        return rpm_alert_color(value)
    if param == PARAM_BOOST:
        return boost_alert_color(value)
    if param == PARAM_MAT:
        return mat_alert_color(value)
    if param == PARAM_TPS:
        return tps_alert_color(value)
    return COLOR_VALUE  # no thresholds defined for this channel - plain white


# ============================================================================
#  Touch / tap-zone configuration
# ============================================================================
# Resistive touch on this panel is too clunky for reliable drag/swipe
# gestures, so navigation is done with tap zones instead: a firm tap on the
# left edge goes to the previous page, a tap on the right edge goes to the
# next page. The middle of the screen does nothing.
#
# Raw 12-bit ADC calibration for the resistive panel (0-4094 valid, 4095 = no
# touch). Only used to figure out which zone (left/right) a tap landed in, so
# precision doesn't matter much. Measured on this panel in portrait mode:
# raw x ~3760 at the physical left edge, ~250 at the right edge (i.e. raw x
# DECREASES left-to-right on this panel/rotation) - hence MIN > MAX below.
# The mapping math handles that fine either way. If the zones feel reversed,
# swap these two.
TS_RAW_X_MIN = 3760
TS_RAW_X_MAX = 250

# Minimum raw pressure (TSC2007 "Z1" reading) to count as a real press. The
# driver's own default threshold is 100; raise this if light brushes/vibration
# false-trigger, lower it if firm presses aren't registering.
TOUCH_PRESSURE_THRESHOLD = 100

# Left/right tap zones, as a fraction of screen width from each edge. 0.5 means
# the two zones meet in the middle - the whole screen is either "previous" or
# "next", with no dead zone.
TAP_ZONE_FRACTION = 0.5

# ============================================================================
#  Display / UI layout (portrait: narrower and taller than the panel's
#  landscape default, so the vertical spacing below is laid out for that)
# ============================================================================
SCREEN_W = 240
SCREEN_H = 320

COLOR_BG = 0x000000
COLOR_TITLE = 0x00FFFF
COLOR_VALUE = 0xFFFFFF
COLOR_VALUE_STALE = 0xFF0000
COLOR_UNITS = 0xFFFF00
COLOR_PEAK = 0xFF8000
COLOR_DOT_ACTIVE = 0xFFFFFF
COLOR_DOT_INACTIVE = 0x404040
COLOR_WARNING = 0xFF0000

TITLE_CENTER_Y = 28
TITLE_SCALE = 2

VALUE_CENTER_Y = 140
VALUE_SCALE = 6

UNITS_CENTER_Y = 215
UNITS_SCALE = 2

PEAK_CENTER_Y = 255
PEAK_SCALE = 2

DOTS_Y = 300
DOT_SIZE = 8
DOT_SPACING = 22

# ============================================================================
#  Parameters / pages
# ============================================================================
PAGES = [
    {"name": "RPM", "units": "RPM", "decimals": 0},
    {"name": "MAP", "units": "kPa", "decimals": 1},
    {"name": "BOOST", "units": "PSI", "decimals": 1},
    {"name": "COOLANT", "units": "F", "decimals": 0},
    {"name": "TPS", "units": "%", "decimals": 1},
    {"name": "AFR 1", "units": "AFR", "decimals": 1},
    {"name": "BATTERY", "units": "V", "decimals": 1},
    {"name": "MAT", "units": "F", "decimals": 0},
]
NUM_PARAMS = len(PAGES)
(
    PARAM_RPM, PARAM_MAP, PARAM_BOOST, PARAM_CLT, PARAM_TPS, PARAM_AFR, PARAM_BATT,
    PARAM_MAT,
) = range(NUM_PARAMS)

# Two extra "pages" beyond the 8 single-gauge ones: a 2x2 overview grid, and
# a Boost/MAT dual beam-gauge page. (AFR and MAP each get their own inline
# beam bar on their existing single-gauge page instead of a separate page.)
OVERVIEW_PAGE = NUM_PARAMS
BEAM_PAGE = NUM_PARAMS + 1
PAGE_COUNT = NUM_PARAMS + 2

live_data = [{"value": 0.0, "last_update": None} for _ in range(NUM_PARAMS)]

# Peak-hold: highest (and lowest) value seen per parameter since power-on.
# Kept separate from live_data/staleness on purpose - once set, a peak stays
# displayed even if the live value goes stale, and only clears on a power
# cycle/reset. low_value is only actually displayed on the AFR page (as a
# "low peak" alongside the high peak), but is tracked for every channel since
# it's practically free.
peak_value = [None] * NUM_PARAMS
low_value = [None] * NUM_PARAMS

# Running average (mean since power-on) per parameter. Only actually displayed
# on the Battery page - a battery's average system voltage is more telling
# than a momentary peak - but tracked for every channel since it's cheap.
avg_sum = [0.0] * NUM_PARAMS
avg_count = [0] * NUM_PARAMS


def set_live(param, value, now):
    live_data[param]["value"] = value
    live_data[param]["last_update"] = now
    if peak_value[param] is None or value > peak_value[param]:
        peak_value[param] = value
    if low_value[param] is None or value < low_value[param]:
        low_value[param] = value
    avg_sum[param] += value
    avg_count[param] += 1


def average_value(param):
    if avg_count[param] == 0:
        return None
    return avg_sum[param] / avg_count[param]


def is_stale(param):
    last = live_data[param]["last_update"]
    if last is None:
        return True
    return (time.monotonic() - last) > STALE_TIMEOUT_S


def format_number(param, value):
    if PAGES[param]["decimals"] == 0:
        return "{:.0f}".format(value)
    return "{:.1f}".format(value)


def format_value(param):
    if is_stale(param):
        return "---"
    return format_number(param, live_data[param]["value"])


def format_peak(param):
    peak = peak_value[param]
    if peak is None:
        return "---"
    return format_number(param, peak)


# ============================================================================
#  Display setup
# ============================================================================
displayio.release_displays()

spi = board.SPI()
tft_cs = board.D9
tft_dc = board.D10

display_bus = fourwire.FourWire(spi, command=tft_dc, chip_select=tft_cs, reset=board.D6)
# rotation=0 (the previous default) matched this panel's native landscape
# mounting, so getting portrait needs an explicit 90-degree turn - not just
# swapping width/height. If it comes out upside-down/mirrored, try 270 instead.
DISPLAY_ROTATION = 90
display = adafruit_ili9341.ILI9341(
    display_bus, width=SCREEN_W, height=SCREEN_H, rotation=DISPLAY_ROTATION
)

# ----------------------------------------------------------------------------
# Boot splash - shown briefly before the dash UI takes over. Must be a 16-bit
# (or indexed) uncompressed BMP - displayio's OnDiskBitmap doesn't reliably
# support plain 24/32-bit true-color BMPs.
# ----------------------------------------------------------------------------
SPLASH_IMAGE_PATH = "/splash.bmp"
SPLASH_DURATION_S = 2.0

try:
    _splash_bitmap = displayio.OnDiskBitmap(SPLASH_IMAGE_PATH)
    _splash_tile = displayio.TileGrid(_splash_bitmap, pixel_shader=_splash_bitmap.pixel_shader)
    _boot_splash_group = displayio.Group()
    _boot_splash_group.append(_splash_tile)
    display.root_group = _boot_splash_group
    time.sleep(SPLASH_DURATION_S)
except OSError as e:  # pylint: disable=broad-except
    print("Splash image not found/failed to load:", e)

splash = displayio.Group()
display.root_group = splash

bg_bitmap = displayio.Bitmap(SCREEN_W, SCREEN_H, 1)
bg_palette = displayio.Palette(1)
bg_palette[0] = COLOR_BG
splash.append(displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette, x=0, y=0))

title_label = label.Label(
    terminalio.FONT, text="", color=COLOR_TITLE, scale=TITLE_SCALE,
    anchor_point=(0.5, 0.5), anchored_position=(SCREEN_W // 2, TITLE_CENTER_Y),
)
splash.append(title_label)

value_label = label.Label(
    terminalio.FONT, text="", color=COLOR_VALUE, scale=VALUE_SCALE,
    anchor_point=(0.5, 0.5), anchored_position=(SCREEN_W // 2, VALUE_CENTER_Y),
)
splash.append(value_label)

units_label = label.Label(
    terminalio.FONT, text="", color=COLOR_UNITS, scale=UNITS_SCALE,
    anchor_point=(0.5, 0.5), anchored_position=(SCREEN_W // 2, UNITS_CENTER_Y),
)
splash.append(units_label)

peak_label = label.Label(
    terminalio.FONT, text="", color=COLOR_PEAK, scale=PEAK_SCALE,
    anchor_point=(0.5, 0.5), anchored_position=(SCREEN_W // 2, PEAK_CENTER_Y),
)
splash.append(peak_label)

warning_label = label.Label(
    terminalio.FONT, text="", color=COLOR_WARNING, scale=1,
    anchor_point=(1.0, 0.0), anchored_position=(SCREEN_W - 4, 4),
)
splash.append(warning_label)

# Datalogger status - top-left corner, mirroring the "NO CAN" banner's style
# but always shown (not just when there's a problem), on every page including
# the overview grid.
datalog_label = label.Label(
    terminalio.FONT, text="", color=COLOR_DOT_INACTIVE, scale=1,
    anchor_point=(0.0, 0.0), anchored_position=(4, 4),
)
splash.append(datalog_label)

# "<" / ">" tap-zone indicators, dim so they don't distract from the value.
COLOR_ARROW = 0x606060
ARROW_SCALE = 3

left_arrow_label = label.Label(
    terminalio.FONT, text="<", color=COLOR_ARROW, scale=ARROW_SCALE,
    anchor_point=(0.0, 0.5), anchored_position=(6, VALUE_CENTER_Y),
)
splash.append(left_arrow_label)

right_arrow_label = label.Label(
    terminalio.FONT, text=">", color=COLOR_ARROW, scale=ARROW_SCALE,
    anchor_point=(1.0, 0.5), anchored_position=(SCREEN_W - 6, VALUE_CENTER_Y),
)
splash.append(right_arrow_label)

# ----------------------------------------------------------------------------
# Segmented "light bars" - race-style round LED bulbs, one per page (RPM
# shift bar, coolant temp, boost, MAT, TPS). Only one is ever visible at a
# time (same Y position, toggled by update_display()). Each bulb is a small
# filled circle drawn once at setup on a fixed dark "housing" background;
# updates only ever change the circle's own palette color, never its
# shape/position. Lit bulbs are colored for the value AT that bulb's
# position (not the live value), so the bar visually fills through a smooth
# color gradient as the reading climbs - like a real motorsport shift-light
# bar.
# ----------------------------------------------------------------------------
BAR_Y = 78
BAR_SEGMENT_W = 28   # bulb bounding box (square, so the circle isn't squashed)
BAR_SEGMENT_H = 28
BAR_GAP = 4
BAR_HOUSING_COLOR = 0x101010  # fixed dark background behind each bulb, never changes


def draw_filled_circle(bmp, cx, cy, radius, value):
    # bitmaptools.draw_circle() only plots the 1-pixel outline (Bresenham's
    # algorithm), not a solid disk, so a real filled circle needs a manual
    # per-pixel scan instead. This only ever runs once per bulb at setup, so
    # the O(radius^2) cost here is irrelevant to the main loop.
    r_squared = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        if y < 0 or y >= bmp.height:
            continue
        dy = y - cy
        for x in range(cx - radius, cx + radius + 1):
            if x < 0 or x >= bmp.width:
                continue
            dx = x - cx
            if dx * dx + dy * dy <= r_squared:
                bmp[x, y] = value


def make_segment_bar(segment_count):
    group = displayio.Group()
    palettes = []
    total_w = segment_count * BAR_SEGMENT_W + (segment_count - 1) * BAR_GAP
    start_x = (SCREEN_W - total_w) // 2
    radius = BAR_SEGMENT_W // 2 - 1  # nearly fills the bounding box
    cx = BAR_SEGMENT_W // 2
    cy = BAR_SEGMENT_H // 2
    for i in range(segment_count):
        # value_count=2: index 0 = housing (fixed), index 1 = the bulb itself
        # (its color is the only thing update_segment_bar() ever changes).
        bmp = displayio.Bitmap(BAR_SEGMENT_W, BAR_SEGMENT_H, 2)
        draw_filled_circle(bmp, cx, cy, radius, 1)
        pal = displayio.Palette(2)
        pal[0] = BAR_HOUSING_COLOR
        pal[1] = COLOR_DOT_INACTIVE  # starts unlit
        x = start_x + i * (BAR_SEGMENT_W + BAR_GAP)
        y = BAR_Y - BAR_SEGMENT_H // 2
        group.append(displayio.TileGrid(bmp, pixel_shader=pal, x=x, y=y))
        palettes.append(pal)
    splash.append(group)
    return group, palettes


def update_segment_bar(palettes, value, value_min, value_max, color_func):
    segment_count = len(palettes)
    span = value_max - value_min
    lit_count = int(((value - value_min) / span) * segment_count)
    if lit_count < 0:
        lit_count = 0
    elif lit_count > segment_count:
        lit_count = segment_count
    for i, pal in enumerate(palettes):
        if i < lit_count:
            segment_value = value_min + i * (span / segment_count)
            pal[1] = color_func(segment_value)
        else:
            pal[1] = COLOR_DOT_INACTIVE


SHIFT_BAR_SEGMENTS = 7
SHIFT_BAR_MAX_RPM = 7000  # bar is "full" at this RPM - adjust to your redline
shift_bar_group, shift_bar_palettes = make_segment_bar(SHIFT_BAR_SEGMENTS)

# Coolant bar range - wide enough to show cold start through overheat.
# (The bar itself is a continuous beam built further down alongside the other
# inline beam bars, not a round-LED segment bar.)
CLT_BAR_MIN_F = 100.0
CLT_BAR_MAX_F = 250.0


# MAT bar range - a bit of headroom below/above the green->red zone.
# (The bar itself is a continuous beam built further down alongside the other
# inline beam bars, not a round-LED segment bar.)
MAT_BAR_MIN_F = 60.0
MAT_BAR_MAX_F = 200.0

# TPS bar - 0-100%. No real "danger zone", but it still gets a gradient
# (blue->green->yellow) purely for visual feedback/consistency with the rest.
# (The bar itself is a continuous beam built further down alongside the other
# inline beam bars, not a round-LED segment bar.)
TPS_BAR_MIN_PCT = 0.0
TPS_BAR_MAX_PCT = 100.0


# ----------------------------------------------------------------------------
# Overview grid page - Boost / Coolant / AFR / Battery, 2x2, filling the whole
# screen (no title text on this page - the 4 labels speak for themselves).
# Units are left off the value text to leave room for a bigger number; the
# name label above each value already identifies the channel.
# ----------------------------------------------------------------------------
GRID_PARAMS = [PARAM_BOOST, PARAM_CLT, PARAM_AFR, PARAM_BATT]
GRID_NAME_SCALE = 2
GRID_VALUE_SCALE = 3
GRID_NAME_POS = [(60, 55), (180, 55), (60, 195), (180, 195)]
GRID_VALUE_POS = [(60, 105), (180, 105), (60, 245), (180, 245)]

overview_group = displayio.Group()
overview_value_labels = []
for _i, _param in enumerate(GRID_PARAMS):
    _nx, _ny = GRID_NAME_POS[_i]
    _vx, _vy = GRID_VALUE_POS[_i]
    _name_label = label.Label(
        terminalio.FONT, text=PAGES[_param]["name"], color=COLOR_UNITS, scale=GRID_NAME_SCALE,
        anchor_point=(0.5, 0.5), anchored_position=(_nx, _ny),
    )
    overview_group.append(_name_label)
    _value_label = label.Label(
        terminalio.FONT, text="", color=COLOR_VALUE, scale=GRID_VALUE_SCALE,
        anchor_point=(0.5, 0.5), anchored_position=(_vx, _vy),
    )
    overview_group.append(_value_label)
    overview_value_labels.append(_value_label)
overview_group.hidden = True
splash.append(overview_group)


def update_overview():
    for i, param in enumerate(GRID_PARAMS):
        lbl = overview_value_labels[i]
        if is_stale(param):
            text = "---"
            color = COLOR_VALUE_STALE
        else:
            value = live_data[param]["value"]
            text = format_number(param, value)
            color = get_value_color(param, value)
        if lbl.text != text:
            lbl.text = text
        lbl.color = color


# ----------------------------------------------------------------------------
# Boost/MAT beam-gauge page - two continuous "beam" fill bars (label + big
# number + a solid-color bar that fills left-to-right), stacked vertically.
# Unlike the segmented shift-light-style bars, this is one continuous filled
# region drawn with bitmaptools.fill_region() - closer to a classic VU meter.
# ----------------------------------------------------------------------------
BEAM_BAR_W = 210
BEAM_BAR_H = 45
BEAM_BAR_X = (SCREEN_W - BEAM_BAR_W) // 2
BEAM_BORDER_PX = 2
BEAM_BORDER_COLOR = 0xAAAAAA

# Peak-hold marker: a thin vertical line at the highest value ever reached
# (same power-on-duration peak_value[] used for the "PEAK ###" text on the
# single-gauge pages), drawn on top of the fill so it's visible whether the
# current value is above or below it.
BEAM_PEAK_MARKER_COLOR = 0xFFA500
BEAM_PEAK_MARKER_W = 3

BEAM_LABEL_SCALE = 2
BEAM_VALUE_SCALE = 4

BEAM_TOP_TEXT_Y = 45   # Boost label/value row
BEAM_TOP_BAR_Y = 75    # Boost bar top edge
BEAM_BOT_TEXT_Y = 195  # MAT label/value row
BEAM_BOT_BAR_Y = 225   # MAT bar top edge


def make_beam_bar(group, bar_y):
    # Border: a slightly larger single-color bitmap sitting behind the fill
    # bar, so a BEAM_BORDER_PX-wide frame shows around it.
    border_bmp = displayio.Bitmap(BEAM_BAR_W + 2 * BEAM_BORDER_PX, BEAM_BAR_H + 2 * BEAM_BORDER_PX, 1)
    border_pal = displayio.Palette(1)
    border_pal[0] = BEAM_BORDER_COLOR
    group.append(displayio.TileGrid(
        border_bmp, pixel_shader=border_pal,
        x=BEAM_BAR_X - BEAM_BORDER_PX, y=bar_y - BEAM_BORDER_PX,
    ))

    fill_bmp = displayio.Bitmap(BEAM_BAR_W, BEAM_BAR_H, 3)
    fill_pal = displayio.Palette(3)
    fill_pal[0] = COLOR_BG
    fill_pal[1] = COLOR_VALUE
    fill_pal[2] = BEAM_PEAK_MARKER_COLOR
    group.append(displayio.TileGrid(fill_bmp, pixel_shader=fill_pal, x=BEAM_BAR_X, y=bar_y))
    return fill_bmp, fill_pal


beam_group = displayio.Group()

boost_beam_label = label.Label(
    terminalio.FONT, text="BOOST", color=COLOR_UNITS, scale=BEAM_LABEL_SCALE,
    anchor_point=(0.0, 0.5), anchored_position=(BEAM_BAR_X, BEAM_TOP_TEXT_Y),
)
beam_group.append(boost_beam_label)
boost_beam_value = label.Label(
    terminalio.FONT, text="", color=COLOR_VALUE, scale=BEAM_VALUE_SCALE,
    anchor_point=(1.0, 0.5), anchored_position=(BEAM_BAR_X + BEAM_BAR_W, BEAM_TOP_TEXT_Y),
)
beam_group.append(boost_beam_value)
boost_beam_bitmap, boost_beam_palette = make_beam_bar(beam_group, BEAM_TOP_BAR_Y)

mat_beam_label = label.Label(
    terminalio.FONT, text="MAT", color=COLOR_UNITS, scale=BEAM_LABEL_SCALE,
    anchor_point=(0.0, 0.5), anchored_position=(BEAM_BAR_X, BEAM_BOT_TEXT_Y),
)
beam_group.append(mat_beam_label)
mat_beam_value = label.Label(
    terminalio.FONT, text="", color=COLOR_VALUE, scale=BEAM_VALUE_SCALE,
    anchor_point=(1.0, 0.5), anchored_position=(BEAM_BAR_X + BEAM_BAR_W, BEAM_BOT_TEXT_Y),
)
beam_group.append(mat_beam_value)
mat_beam_bitmap, mat_beam_palette = make_beam_bar(beam_group, BEAM_BOT_BAR_Y)

beam_group.hidden = True
splash.append(beam_group)


def update_beam(param, value_lbl, bitmap, palette, value_min, value_max, color_func,
                 bar_w=None, bar_h=None):
    if bar_w is None:
        bar_w = BEAM_BAR_W
    if bar_h is None:
        bar_h = BEAM_BAR_H

    stale = is_stale(param)
    if stale:
        text = "---"
        fill_frac = 0.0
    else:
        value = live_data[param]["value"]
        text = format_number(param, value)
        fill_frac = (value - value_min) / (value_max - value_min)
        if fill_frac < 0:
            fill_frac = 0.0
        elif fill_frac > 1:
            fill_frac = 1.0
        palette[1] = color_func(value)

    if value_lbl.text != text:
        value_lbl.text = text
    value_lbl.color = COLOR_VALUE_STALE if stale else COLOR_VALUE

    fill_px = int(fill_frac * bar_w)
    if fill_px > 0:
        bitmaptools.fill_region(bitmap, 0, 0, fill_px, bar_h, 1)
    if fill_px < bar_w:
        bitmaptools.fill_region(bitmap, fill_px, 0, bar_w, bar_h, 0)

    # Peak marker drawn last, on top of the fill/background either way.
    peak = peak_value[param]
    if peak is not None:
        peak_frac = (peak - value_min) / (value_max - value_min)
        if peak_frac < 0:
            peak_frac = 0.0
        elif peak_frac > 1:
            peak_frac = 1.0
        marker_start = int(peak_frac * bar_w)
        marker_end = marker_start + BEAM_PEAK_MARKER_W
        if marker_end > bar_w:
            marker_end = bar_w
            marker_start = max(0, marker_end - BEAM_PEAK_MARKER_W)
        bitmaptools.fill_region(bitmap, marker_start, 0, marker_end, bar_h, 2)


def update_beam_page():
    update_beam(
        PARAM_BOOST, boost_beam_value, boost_beam_bitmap, boost_beam_palette,
        BOOST_MIN_PSI, BOOST_MAX_PSI, boost_alert_color,
    )
    update_beam(
        PARAM_MAT, mat_beam_value, mat_beam_bitmap, mat_beam_palette,
        MAT_BAR_MIN_F, MAT_BAR_MAX_F, mat_alert_color,
    )


# ----------------------------------------------------------------------------
# AFR + MAP + Battery inline beam bars - same continuous-fill visual style as
# the Boost/MAT beam page, but sized to sit in the same "light bar slot"
# above the main value on the AFR, MAP, and Battery single-gauge pages
# themselves (like the round-LED bars do for RPM/Coolant/Boost/MAT/TPS), not
# a separate page.
#
# AFR is different from every other bar: instead of filling from the left
# edge, 14.7 (stoich) is pinned to the exact horizontal CENTER of the bar,
# with a fixed reference tick drawn there, and a moving needle line marks the
# current AFR - sliding left into the rich side (9-14.7) or right into the
# lean side (14.7-18), each side using its own linear scale so the center
# point stays fixed regardless of how lopsided the two ranges are (5.7 AFR
# on the rich side vs. 3.3 on the lean side).
# ----------------------------------------------------------------------------
INLINE_BAR_W = 210
INLINE_BAR_H = 30
INLINE_BAR_X = (SCREEN_W - INLINE_BAR_W) // 2
# Same top edge as the round-LED bars, so they align on the page.
INLINE_BAR_TOP_Y = BAR_Y - BAR_SEGMENT_H // 2

AFR_BAR_MIN = 9.0
AFR_BAR_CENTER = 14.7
AFR_BAR_MAX = 18.0  # most wideband AFR gauges span roughly this range

AFR_CENTER_TICK_COLOR = 0xAAAAAA
AFR_CENTER_TICK_W = 2
# Low (richest) and high (leanest) peak-hold markers on the AFR bar - thin
# orange lines at the furthest-left and furthest-right positions the fill has
# reached since power-on. Drawn as their own TileGrids over the fill (see
# update_afr_marker) so they stay steady instead of flickering.
AFR_PEAK_MARKER_W = 3

MAP_BAR_MIN = 0.0
MAP_BAR_MAX = 250.0

BATT_BAR_MIN = 10.0
BATT_BAR_MAX = 16.0
# Average-voltage marker on the Battery bar - a thin orange line (its own
# TileGrid, like the AFR peak markers) that slides smoothly to the running
# average instead of flickering as the fill redraws.
BATT_AVG_MARKER_W = 3


def afr_to_beam_x(afr, bar_w=INLINE_BAR_W):
    # Maps AFR_BAR_MIN..AFR_BAR_CENTER onto the left half of the bar and
    # AFR_BAR_CENTER..AFR_BAR_MAX onto the right half, so the center value
    # always lands exactly at the halfway pixel regardless of the two
    # sides' differing spans.
    half = bar_w / 2
    if afr <= AFR_BAR_CENTER:
        frac = (afr - AFR_BAR_MIN) / (AFR_BAR_CENTER - AFR_BAR_MIN)
        if frac < 0:
            frac = 0.0
        elif frac > 1:
            frac = 1.0
        return frac * half
    frac = (afr - AFR_BAR_CENTER) / (AFR_BAR_MAX - AFR_BAR_CENTER)
    if frac < 0:
        frac = 0.0
    elif frac > 1:
        frac = 1.0
    return half + frac * half


def update_afr_marker(marker_tg, peak, bar_w):
    # The low/high peak markers are their own TileGrids layered over the fill
    # bitmap, moved only when a new extreme is reached (not redrawn every
    # frame), so they sit rock-steady instead of flickering as the fill under
    # them redraws.
    if peak is None:
        if not marker_tg.hidden:
            marker_tg.hidden = True
        return
    mx = int(afr_to_beam_x(peak, bar_w))
    lo = mx - AFR_PEAK_MARKER_W // 2
    if lo < 0:
        lo = 0
    elif lo > bar_w - AFR_PEAK_MARKER_W:
        lo = bar_w - AFR_PEAK_MARKER_W
    new_x = INLINE_BAR_X + lo
    if marker_tg.hidden:
        marker_tg.hidden = False
    if marker_tg.x != new_x:
        marker_tg.x = new_x


def update_afr_bar(bitmap, palette, low_marker, high_marker,
                   bar_w=INLINE_BAR_W, bar_h=INLINE_BAR_H):
    # AFR's page is a normal single-gauge page, so the main value_label already
    # shows the big number - this only draws the bar. The fill emanates from
    # the center (stoich, 14.7) outward toward the current AFR: rich (below
    # 14.7) fills to the left of center, lean (above) fills to the right, so
    # both how far and which way it's off stoich read at a glance. The fill is
    # colored by afr_alert_color, so it also shifts green->yellow->red as it
    # leans out.
    stale = is_stale(PARAM_AFR)
    center_x = int(bar_w / 2)

    if stale:
        bitmaptools.fill_region(bitmap, 0, 0, bar_w, bar_h, 0)
    else:
        value = live_data[PARAM_AFR]["value"]
        palette[2] = afr_alert_color(value)
        pos_x = int(afr_to_beam_x(value, bar_w))
        if pos_x < center_x:
            fill_lo, fill_hi = pos_x, center_x
        else:
            fill_lo, fill_hi = center_x, pos_x
        # Paint background on both sides and the fill in the middle - every
        # pixel is written once per frame (no clear-then-redraw), so the fill
        # doesn't flash as it moves.
        if fill_lo > 0:
            bitmaptools.fill_region(bitmap, 0, 0, fill_lo, bar_h, 0)
        if fill_hi < bar_w:
            bitmaptools.fill_region(bitmap, fill_hi, 0, bar_w, bar_h, 0)
        if fill_hi > fill_lo:
            bitmaptools.fill_region(bitmap, fill_lo, 0, fill_hi, bar_h, 2)

    # Center (stoich) reference tick, drawn on top of the fill so the fixed
    # 14.7 origin stays visible.
    tick_start = max(0, center_x - AFR_CENTER_TICK_W // 2)
    tick_end = min(bar_w, tick_start + AFR_CENTER_TICK_W)
    bitmaptools.fill_region(bitmap, tick_start, 0, tick_end, bar_h, 1)

    update_afr_marker(low_marker, low_value[PARAM_AFR], bar_w)
    update_afr_marker(high_marker, peak_value[PARAM_AFR], bar_w)


def update_map_bar(bitmap, palette):
    # Same drawing logic as update_beam(), minus the value-label handling -
    # MAP's page already shows its own big number generically.
    stale = is_stale(PARAM_MAP)
    if stale:
        fill_frac = 0.0
    else:
        value = live_data[PARAM_MAP]["value"]
        fill_frac = (value - MAP_BAR_MIN) / (MAP_BAR_MAX - MAP_BAR_MIN)
        if fill_frac < 0:
            fill_frac = 0.0
        elif fill_frac > 1:
            fill_frac = 1.0
        palette[1] = map_alert_color(value)

    fill_px = int(fill_frac * INLINE_BAR_W)
    if fill_px > 0:
        bitmaptools.fill_region(bitmap, 0, 0, fill_px, INLINE_BAR_H, 1)
    if fill_px < INLINE_BAR_W:
        bitmaptools.fill_region(bitmap, fill_px, 0, INLINE_BAR_W, INLINE_BAR_H, 0)

    peak = peak_value[PARAM_MAP]
    if peak is not None:
        peak_frac = (peak - MAP_BAR_MIN) / (MAP_BAR_MAX - MAP_BAR_MIN)
        if peak_frac < 0:
            peak_frac = 0.0
        elif peak_frac > 1:
            peak_frac = 1.0
        marker_start = int(peak_frac * INLINE_BAR_W)
        marker_end = marker_start + BEAM_PEAK_MARKER_W
        if marker_end > INLINE_BAR_W:
            marker_end = INLINE_BAR_W
            marker_start = max(0, marker_end - BEAM_PEAK_MARKER_W)
        bitmaptools.fill_region(bitmap, marker_start, 0, marker_end, INLINE_BAR_H, 2)


def update_batt_avg_marker(marker_tg, avg):
    # Slides the average-voltage marker (its own TileGrid) to the running
    # average, moving only when the pixel position actually changes so it
    # updates smoothly without flickering as the fill under it redraws.
    if avg is None:
        if not marker_tg.hidden:
            marker_tg.hidden = True
        return
    frac = (avg - BATT_BAR_MIN) / (BATT_BAR_MAX - BATT_BAR_MIN)
    if frac < 0:
        frac = 0.0
    elif frac > 1:
        frac = 1.0
    lo = int(frac * INLINE_BAR_W) - BATT_AVG_MARKER_W // 2
    if lo < 0:
        lo = 0
    elif lo > INLINE_BAR_W - BATT_AVG_MARKER_W:
        lo = INLINE_BAR_W - BATT_AVG_MARKER_W
    new_x = INLINE_BAR_X + lo
    if marker_tg.hidden:
        marker_tg.hidden = False
    if marker_tg.x != new_x:
        marker_tg.x = new_x


def update_batt_bar(bitmap, palette, avg_marker):
    # Same left-fill pattern as update_map_bar(), but colored by the battery
    # gradient (blue resting / green charging / red low or overcharging)
    # instead of a flat color. The marker on this bar is the running average
    # (a separate TileGrid), not a peak.
    stale = is_stale(PARAM_BATT)
    if stale:
        fill_frac = 0.0
    else:
        value = live_data[PARAM_BATT]["value"]
        fill_frac = (value - BATT_BAR_MIN) / (BATT_BAR_MAX - BATT_BAR_MIN)
        if fill_frac < 0:
            fill_frac = 0.0
        elif fill_frac > 1:
            fill_frac = 1.0
        palette[1] = battery_alert_color(value)

    fill_px = int(fill_frac * INLINE_BAR_W)
    if fill_px > 0:
        bitmaptools.fill_region(bitmap, 0, 0, fill_px, INLINE_BAR_H, 1)
    if fill_px < INLINE_BAR_W:
        bitmaptools.fill_region(bitmap, fill_px, 0, INLINE_BAR_W, INLINE_BAR_H, 0)

    update_batt_avg_marker(avg_marker, average_value(PARAM_BATT))


def update_boost_bar(bitmap, palette):
    # Same left-fill pattern as update_map_bar()/update_batt_bar(), colored
    # by the boost gradient (flat blue through BOOST_BLUE_MAX_PSI, fading to
    # red by BOOST_MAX_PSI).
    stale = is_stale(PARAM_BOOST)
    if stale:
        fill_frac = 0.0
    else:
        value = live_data[PARAM_BOOST]["value"]
        fill_frac = (value - BOOST_MIN_PSI) / (BOOST_MAX_PSI - BOOST_MIN_PSI)
        if fill_frac < 0:
            fill_frac = 0.0
        elif fill_frac > 1:
            fill_frac = 1.0
        palette[1] = boost_alert_color(value)

    fill_px = int(fill_frac * INLINE_BAR_W)
    if fill_px > 0:
        bitmaptools.fill_region(bitmap, 0, 0, fill_px, INLINE_BAR_H, 1)
    if fill_px < INLINE_BAR_W:
        bitmaptools.fill_region(bitmap, fill_px, 0, INLINE_BAR_W, INLINE_BAR_H, 0)

    peak = peak_value[PARAM_BOOST]
    if peak is not None:
        peak_frac = (peak - BOOST_MIN_PSI) / (BOOST_MAX_PSI - BOOST_MIN_PSI)
        if peak_frac < 0:
            peak_frac = 0.0
        elif peak_frac > 1:
            peak_frac = 1.0
        marker_start = int(peak_frac * INLINE_BAR_W)
        marker_end = marker_start + BEAM_PEAK_MARKER_W
        if marker_end > INLINE_BAR_W:
            marker_end = INLINE_BAR_W
            marker_start = max(0, marker_end - BEAM_PEAK_MARKER_W)
        bitmaptools.fill_region(bitmap, marker_start, 0, marker_end, INLINE_BAR_H, 2)


def update_clt_bar(bitmap, palette):
    # Same left-fill pattern as the other inline beam bars, colored by the
    # coolant gradient (blue cold / green normal / yellow->red hot).
    stale = is_stale(PARAM_CLT)
    if stale:
        fill_frac = 0.0
    else:
        value = live_data[PARAM_CLT]["value"]
        fill_frac = (value - CLT_BAR_MIN_F) / (CLT_BAR_MAX_F - CLT_BAR_MIN_F)
        if fill_frac < 0:
            fill_frac = 0.0
        elif fill_frac > 1:
            fill_frac = 1.0
        palette[1] = clt_alert_color(value)

    fill_px = int(fill_frac * INLINE_BAR_W)
    if fill_px > 0:
        bitmaptools.fill_region(bitmap, 0, 0, fill_px, INLINE_BAR_H, 1)
    if fill_px < INLINE_BAR_W:
        bitmaptools.fill_region(bitmap, fill_px, 0, INLINE_BAR_W, INLINE_BAR_H, 0)

    peak = peak_value[PARAM_CLT]
    if peak is not None:
        peak_frac = (peak - CLT_BAR_MIN_F) / (CLT_BAR_MAX_F - CLT_BAR_MIN_F)
        if peak_frac < 0:
            peak_frac = 0.0
        elif peak_frac > 1:
            peak_frac = 1.0
        marker_start = int(peak_frac * INLINE_BAR_W)
        marker_end = marker_start + BEAM_PEAK_MARKER_W
        if marker_end > INLINE_BAR_W:
            marker_end = INLINE_BAR_W
            marker_start = max(0, marker_end - BEAM_PEAK_MARKER_W)
        bitmaptools.fill_region(bitmap, marker_start, 0, marker_end, INLINE_BAR_H, 2)


def update_tps_bar(bitmap, palette):
    # Same left-fill pattern as the other inline beam bars, colored by the
    # TPS gradient (blue closed / green / yellow wide-open).
    stale = is_stale(PARAM_TPS)
    if stale:
        fill_frac = 0.0
    else:
        value = live_data[PARAM_TPS]["value"]
        fill_frac = (value - TPS_BAR_MIN_PCT) / (TPS_BAR_MAX_PCT - TPS_BAR_MIN_PCT)
        if fill_frac < 0:
            fill_frac = 0.0
        elif fill_frac > 1:
            fill_frac = 1.0
        palette[1] = tps_alert_color(value)

    fill_px = int(fill_frac * INLINE_BAR_W)
    if fill_px > 0:
        bitmaptools.fill_region(bitmap, 0, 0, fill_px, INLINE_BAR_H, 1)
    if fill_px < INLINE_BAR_W:
        bitmaptools.fill_region(bitmap, fill_px, 0, INLINE_BAR_W, INLINE_BAR_H, 0)

    peak = peak_value[PARAM_TPS]
    if peak is not None:
        peak_frac = (peak - TPS_BAR_MIN_PCT) / (TPS_BAR_MAX_PCT - TPS_BAR_MIN_PCT)
        if peak_frac < 0:
            peak_frac = 0.0
        elif peak_frac > 1:
            peak_frac = 1.0
        marker_start = int(peak_frac * INLINE_BAR_W)
        marker_end = marker_start + BEAM_PEAK_MARKER_W
        if marker_end > INLINE_BAR_W:
            marker_end = INLINE_BAR_W
            marker_start = max(0, marker_end - BEAM_PEAK_MARKER_W)
        bitmaptools.fill_region(bitmap, marker_start, 0, marker_end, INLINE_BAR_H, 2)


def update_mat_bar(bitmap, palette):
    # Same left-fill pattern as the other inline beam bars, colored by the
    # MAT gradient (green cool / fading to red hot).
    stale = is_stale(PARAM_MAT)
    if stale:
        fill_frac = 0.0
    else:
        value = live_data[PARAM_MAT]["value"]
        fill_frac = (value - MAT_BAR_MIN_F) / (MAT_BAR_MAX_F - MAT_BAR_MIN_F)
        if fill_frac < 0:
            fill_frac = 0.0
        elif fill_frac > 1:
            fill_frac = 1.0
        palette[1] = mat_alert_color(value)

    fill_px = int(fill_frac * INLINE_BAR_W)
    if fill_px > 0:
        bitmaptools.fill_region(bitmap, 0, 0, fill_px, INLINE_BAR_H, 1)
    if fill_px < INLINE_BAR_W:
        bitmaptools.fill_region(bitmap, fill_px, 0, INLINE_BAR_W, INLINE_BAR_H, 0)

    peak = peak_value[PARAM_MAT]
    if peak is not None:
        peak_frac = (peak - MAT_BAR_MIN_F) / (MAT_BAR_MAX_F - MAT_BAR_MIN_F)
        if peak_frac < 0:
            peak_frac = 0.0
        elif peak_frac > 1:
            peak_frac = 1.0
        marker_start = int(peak_frac * INLINE_BAR_W)
        marker_end = marker_start + BEAM_PEAK_MARKER_W
        if marker_end > INLINE_BAR_W:
            marker_end = INLINE_BAR_W
            marker_start = max(0, marker_end - BEAM_PEAK_MARKER_W)
        bitmaptools.fill_region(bitmap, marker_start, 0, marker_end, INLINE_BAR_H, 2)


def make_inline_bar():
    group = displayio.Group()
    border_bmp = displayio.Bitmap(
        INLINE_BAR_W + 2 * BEAM_BORDER_PX, INLINE_BAR_H + 2 * BEAM_BORDER_PX, 1
    )
    border_pal = displayio.Palette(1)
    border_pal[0] = BEAM_BORDER_COLOR
    group.append(displayio.TileGrid(
        border_bmp, pixel_shader=border_pal,
        x=INLINE_BAR_X - BEAM_BORDER_PX, y=INLINE_BAR_TOP_Y - BEAM_BORDER_PX,
    ))

    fill_bmp = displayio.Bitmap(INLINE_BAR_W, INLINE_BAR_H, 3)
    fill_pal = displayio.Palette(3)
    fill_pal[0] = COLOR_BG
    fill_pal[1] = COLOR_VALUE
    fill_pal[2] = BEAM_PEAK_MARKER_COLOR
    group.append(displayio.TileGrid(fill_bmp, pixel_shader=fill_pal, x=INLINE_BAR_X, y=INLINE_BAR_TOP_Y))
    splash.append(group)
    return group, fill_bmp, fill_pal


afr_bar_group, afr_bar_bitmap, afr_bar_palette = make_inline_bar()
afr_bar_palette[1] = AFR_CENTER_TICK_COLOR


def make_afr_marker():
    # A small solid-orange bar the height of the AFR track. Positioned (and
    # shown/hidden) via its TileGrid, layered above the fill in afr_bar_group.
    bmp = displayio.Bitmap(AFR_PEAK_MARKER_W, INLINE_BAR_H, 1)
    pal = displayio.Palette(1)
    pal[0] = BEAM_PEAK_MARKER_COLOR
    tg = displayio.TileGrid(bmp, pixel_shader=pal, x=INLINE_BAR_X, y=INLINE_BAR_TOP_Y)
    tg.hidden = True
    afr_bar_group.append(tg)
    return tg


afr_low_marker = make_afr_marker()
afr_high_marker = make_afr_marker()

map_bar_group, map_bar_bitmap, map_bar_palette = make_inline_bar()

batt_bar_group, batt_bar_bitmap, batt_bar_palette = make_inline_bar()


def make_batt_avg_marker():
    # Solid-orange marker the height of the battery track, layered above the
    # fill in batt_bar_group; slides to the running average via its TileGrid.
    bmp = displayio.Bitmap(BATT_AVG_MARKER_W, INLINE_BAR_H, 1)
    pal = displayio.Palette(1)
    pal[0] = BEAM_PEAK_MARKER_COLOR
    tg = displayio.TileGrid(bmp, pixel_shader=pal, x=INLINE_BAR_X, y=INLINE_BAR_TOP_Y)
    tg.hidden = True
    batt_bar_group.append(tg)
    return tg


batt_avg_marker = make_batt_avg_marker()

boost_bar_group, boost_bar_bitmap, boost_bar_palette = make_inline_bar()

clt_bar_group, clt_bar_bitmap, clt_bar_palette = make_inline_bar()

tps_bar_group, tps_bar_bitmap, tps_bar_palette = make_inline_bar()

mat_bar_group, mat_bar_bitmap, mat_bar_palette = make_inline_bar()


# Page indicator dots - small squares, recolored (not recreated) on page change.
dot_palettes = []
_dot_total_width = (PAGE_COUNT - 1) * DOT_SPACING
_dot_start_x = (SCREEN_W - _dot_total_width) // 2
for i in range(PAGE_COUNT):
    pal = displayio.Palette(1)
    pal[0] = COLOR_DOT_INACTIVE
    bmp = displayio.Bitmap(DOT_SIZE, DOT_SIZE, 1)
    x = _dot_start_x + i * DOT_SPACING - DOT_SIZE // 2
    y = DOTS_Y - DOT_SIZE // 2
    splash.append(displayio.TileGrid(bmp, pixel_shader=pal, x=x, y=y))
    dot_palettes.append(pal)


def update_dots(active_page):
    for i, pal in enumerate(dot_palettes):
        pal[0] = COLOR_DOT_ACTIVE if i == active_page else COLOR_DOT_INACTIVE


def show_fatal_error(message):
    # Full red screen + message, then halt - mirrors a hard fault, nothing
    # useful can be shown without CAN data anyway.
    err_bitmap = displayio.Bitmap(SCREEN_W, SCREEN_H, 1)
    err_palette = displayio.Palette(1)
    err_palette[0] = 0xFF0000
    err_group = displayio.Group()
    err_group.append(displayio.TileGrid(err_bitmap, pixel_shader=err_palette, x=0, y=0))
    err_group.append(
        label.Label(
            terminalio.FONT, text=message, color=0xFFFFFF, scale=2,
            anchor_point=(0.5, 0.5), anchored_position=(SCREEN_W // 2, SCREEN_H // 2),
        )
    )
    display.root_group = err_group
    print(message)
    while True:
        pass


# ============================================================================
#  Touch setup
# ============================================================================
touch = None
try:
    touch = adafruit_tsc2007.TSC2007(board.I2C())
except Exception as e:  # pylint: disable=broad-except
    print("TSC2007 touch controller not found - tap navigation disabled:", e)


def raw_x_to_screen_x(raw_x):
    sx = (raw_x - TS_RAW_X_MIN) * SCREEN_W / (TS_RAW_X_MAX - TS_RAW_X_MIN)
    if sx < 0:
        sx = 0
    elif sx > SCREEN_W - 1:
        sx = SCREEN_W - 1
    return int(sx)


LEFT_ZONE_MAX_X = int(SCREEN_W * TAP_ZONE_FRACTION)

current_page = PARAM_RPM
force_redraw = True

touch_down = False  # tracks the press that is currently in progress, if any


def change_page(delta):
    global current_page, force_redraw
    current_page = (current_page + delta) % PAGE_COUNT
    force_redraw = True


def handle_touch():
    global touch_down

    if touch is None:
        return

    point = touch.touch
    is_touched = point["pressure"] > TOUCH_PRESSURE_THRESHOLD

    if is_touched and not touch_down:
        # Fresh press (rising edge only, so holding down doesn't repeat-fire).
        touch_down = True
        sx = raw_x_to_screen_x(point["x"])
        if sx <= LEFT_ZONE_MAX_X:
            change_page(-1)  # tapped left half -> previous page
        else:
            change_page(1)  # tapped right half -> next page
    elif not is_touched:
        touch_down = False


# ============================================================================
#  CAN setup + receive
# ============================================================================
# The Feather M4 CAN's onboard transceiver powers up in standby and must be
# explicitly enabled before canio.CAN() will see any bus traffic.
if hasattr(board, "CAN_STANDBY"):
    can_standby = digitalio.DigitalInOut(board.CAN_STANDBY)
    can_standby.switch_to_output(value=False)  # False/LOW = out of standby

if hasattr(board, "BOOST_ENABLE"):
    boost_enable = digitalio.DigitalInOut(board.BOOST_ENABLE)
    boost_enable.switch_to_output(value=True)  # True/HIGH = enable transceiver booster

try:
    can = canio.CAN(tx=board.CAN_TX, rx=board.CAN_RX, baudrate=CAN_BAUD_RATE, auto_restart=True)
except Exception as e:  # pylint: disable=broad-except
    show_fatal_error("CAN INIT FAILED")

can_matches = [
    canio.Match(CAN_ID_DASH0),
    canio.Match(CAN_ID_DASH1),
    canio.Match(CAN_ID_DASH2),
    canio.Match(CAN_ID_DASH3),
]
# timeout=0 makes receive() return immediately (None if nothing queued) instead
# of blocking - required to keep the main loop non-blocking.
can_listener = can.listen(matches=can_matches, timeout=0)


def be16(data, offset):
    # Big-endian (Motorola) signed 16-bit word.
    value = (data[offset] << 8) | data[offset + 1]
    if value >= 0x8000:
        value -= 0x10000
    return value


def decode_frame(msg_id, data):
    now = time.monotonic()

    if msg_id == CAN_ID_DASH0 and len(data) >= 8:
        # Four packed 16-bit words: MAP, RPM, CLT, TPS.
        raw_map = be16(data, OFS_MAP)
        raw_rpm = be16(data, OFS_RPM)  # spec says uint16, but fits signed range fine
        raw_clt = be16(data, OFS_CLT)
        raw_tps = be16(data, OFS_TPS)

        map_kpa = raw_map / SCALE_MAP
        set_live(PARAM_MAP, map_kpa, now)
        set_live(PARAM_RPM, raw_rpm / SCALE_RPM, now)
        set_live(PARAM_CLT, raw_clt / SCALE_CLT, now)
        set_live(PARAM_TPS, raw_tps / SCALE_TPS, now)

        # Boost is derived from MAP, not its own CAN channel - stamped with the
        # same timestamp so it goes stale exactly when MAP does.
        set_live(PARAM_BOOST, (map_kpa - ATMOSPHERIC_KPA) / KPA_PER_PSI, now)

    elif msg_id == CAN_ID_DASH1 and len(data) >= (OFS_MAT + 2):
        raw_mat = be16(data, OFS_MAT)
        set_live(PARAM_MAT, raw_mat / SCALE_MAT, now)

    elif msg_id == CAN_ID_DASH2 and len(data) >= (OFS_AFR1 + 1):
        # AFR1 is a single unsigned byte, not a 16-bit word - no be16() here.
        raw_afr = data[OFS_AFR1]
        set_live(PARAM_AFR, raw_afr / SCALE_AFR1, now)

    elif msg_id == CAN_ID_DASH3 and len(data) >= (OFS_BATT + 2):
        raw_batt = be16(data, OFS_BATT)
        set_live(PARAM_BATT, raw_batt / SCALE_BATT, now)
    # Unrecognized IDs (advance, PW, etc.) are simply ignored.


def poll_can():
    message = can_listener.receive()
    if message is None:
        return  # nothing waiting right now
    data = getattr(message, "data", None)
    if data is None:
        return  # remote-transmission-request frame - no payload
    decode_frame(message.id, data)


# ============================================================================
#  Datalogger - CSV to a physical SD card only, all channels, MegaLogViewer HD
#  compatible (see the note about .mlg at the top of this file)
# ============================================================================
# Deliberately NOT written to the internal CIRCUITPY flash. That flash is
# tiny (~2MB total, shared with the libraries the dash itself needs) and
# writing to it from code requires flipping it read-only to the host PC
# (the exact "media is write protected" lockout we hit during development).
# An SD card sidesteps both problems entirely: it's mounted at its own path
# ("/sd"), completely independent of the CIRCUITPY drive's read/write mode,
# and its capacity doesn't compete with the dash's own code/libraries. If no
# card is inserted, sd_mounted stays False and logging is simply skipped -
# nothing here ever touches internal flash or retains growing state, so a
# missing/full SD card cannot affect the display or leak memory.
SD_CS_PIN = board.D5  # onboard SD slot on the 2.4" TFT FeatherWing

sd_mounted = False
try:
    _sd_cs = digitalio.DigitalInOut(SD_CS_PIN)
    _sdcard = adafruit_sdcard.SDCard(spi, _sd_cs)
    _vfs = storage.VfsFat(_sdcard)
    storage.mount(_vfs, "/sd")
    sd_mounted = True
    print("SD card mounted at /sd")
except Exception as e:  # pylint: disable=broad-except
    print("No SD card detected - datalogging disabled:", e)

# How often to write a row. 10 Hz is plenty for a dash/street-tuning log and
# keeps file growth and writes reasonable - MS logs are typically logged in
# this same 10-20 Hz range.
LOG_INTERVAL_S = 0.1

# A session starts the moment RPM rises above this (engine running/cranked)
# and ends the moment it drops back below it (engine off) - each start gets
# its own log file.
ENGINE_RUNNING_RPM = 300

LOG_DIR = "/sd/logs"


def get_next_log_path():
    try:
        os.mkdir(LOG_DIR)
    except OSError:
        pass  # already exists

    max_n = 0
    for name in os.listdir(LOG_DIR):
        if name.startswith("log") and name.endswith(".csv"):
            try:
                n = int(name[3:-4])
            except ValueError:
                continue
            if n > max_n:
                max_n = n
    return "{}/log{:03d}.csv".format(LOG_DIR, max_n + 1)


log_file = None
log_last_write = 0.0
# Set once a file-open attempt fails, so we don't retry on every single
# engine start/stop for the rest of this boot.
logging_unavailable = not sd_mounted


def start_logging():
    global log_file, log_last_write, logging_unavailable
    if logging_unavailable:
        return
    try:
        path = get_next_log_path()
        log_file = open(path, "w")
        names = ["Time"] + [PAGES[p]["name"] for p in range(NUM_PARAMS)]
        units = ["s"] + [PAGES[p]["units"] for p in range(NUM_PARAMS)]
        log_file.write(",".join(names) + "\n")
        log_file.write(",".join(units) + "\n")
        log_file.flush()
        log_last_write = 0.0
        print("Datalogging started:", path)
    except OSError as e:
        log_file = None
        logging_unavailable = True
        print("Datalogging disabled (SD card write failed):", e)


def stop_logging():
    global log_file
    if log_file is not None:
        log_file.close()
        print("Datalogging stopped.")
        log_file = None


last_datalog_state = None


def update_datalog_indicator():
    global last_datalog_state

    if logging_unavailable:
        state = "nosd"
    elif log_file is not None:
        state = "on"
    else:
        state = "standby"

    if state == last_datalog_state:
        return
    last_datalog_state = state

    if state == "nosd":
        datalog_label.text = "NO SD"
        datalog_label.color = COLOR_WARNING
    elif state == "on":
        datalog_label.text = "LOG ON"
        datalog_label.color = COLOR_ALERT_GREEN
    else:
        datalog_label.text = "LOG OFF"
        datalog_label.color = COLOR_DOT_INACTIVE


def poll_datalogger():
    global log_last_write

    update_datalog_indicator()

    if logging_unavailable:
        return  # no SD card - nothing to do, ever, this boot

    rpm_running = (not is_stale(PARAM_RPM)) and (live_data[PARAM_RPM]["value"] > ENGINE_RUNNING_RPM)

    if rpm_running and log_file is None:
        start_logging()
    elif not rpm_running and log_file is not None:
        stop_logging()

    if log_file is None:
        return

    now = time.monotonic()
    if now - log_last_write < LOG_INTERVAL_S:
        return
    log_last_write = now

    fields = ["{:.2f}".format(now)]
    for p in range(NUM_PARAMS):
        fields.append("" if is_stale(p) else format_number(p, live_data[p]["value"]))
    # Flushing every row costs a little write performance, but in a car the
    # power can disappear with no warning (key off, alternator cutting the
    # 12V-to-5V supply) - better to not lose the last few seconds of a log.
    log_file.write(",".join(fields) + "\n")
    log_file.flush()


# ============================================================================
#  Top-level display update
# ============================================================================
last_value_str = None
last_peak_str = None
last_warning_shown = None


def update_display():
    global last_value_str, last_peak_str, last_warning_shown, force_redraw

    is_overview = current_page == OVERVIEW_PAGE
    is_beam = current_page == BEAM_PAGE
    is_special = is_overview or is_beam  # multi-parameter pages, not a single PAGES[] entry
    is_rpm = current_page == PARAM_RPM
    is_clt = current_page == PARAM_CLT
    is_boost = current_page == PARAM_BOOST
    is_mat = current_page == PARAM_MAT
    is_tps = current_page == PARAM_TPS
    is_afr = current_page == PARAM_AFR
    is_map = current_page == PARAM_MAP
    is_batt = current_page == PARAM_BATT

    if force_redraw:
        title_label.text = "" if is_special else PAGES[current_page]["name"]
        value_label.hidden = is_special
        units_label.hidden = is_special
        peak_label.hidden = is_special
        overview_group.hidden = not is_overview
        beam_group.hidden = not is_beam
        shift_bar_group.hidden = not is_rpm
        clt_bar_group.hidden = not is_clt
        boost_bar_group.hidden = not is_boost
        mat_bar_group.hidden = not is_mat
        tps_bar_group.hidden = not is_tps
        afr_bar_group.hidden = not is_afr
        map_bar_group.hidden = not is_map
        batt_bar_group.hidden = not is_batt
        if not is_special:
            units_label.text = PAGES[current_page]["units"]
        update_dots(current_page)
        last_value_str = None  # sentinel guarantees the value redraws below
        last_peak_str = None
        if is_special:
            # Both special pages have their own per-value stale indication -
            # the global corner banner doesn't apply here, so clear it once
            # on entry.
            warning_label.text = ""
        last_warning_shown = None  # force a fresh NO CAN check too
        force_redraw = False

    if is_overview:
        update_overview()
        return

    if is_beam:
        update_beam_page()
        return

    stale = is_stale(current_page)
    val_str = format_value(current_page)

    if val_str != last_value_str:
        value_label.text = val_str
        if stale:
            value_label.color = COLOR_VALUE_STALE
        else:
            value_label.color = get_value_color(current_page, live_data[current_page]["value"])
        last_value_str = val_str

    if is_afr:
        # AFR gets both a low (richest) and high (leanest) peak readout.
        lo = low_value[PARAM_AFR]
        hi = peak_value[PARAM_AFR]
        lo_str = "---" if lo is None else format_number(PARAM_AFR, lo)
        hi_str = "---" if hi is None else format_number(PARAM_AFR, hi)
        peak_text = "LO {}  HI {}".format(lo_str, hi_str)
    elif is_batt:
        # Battery shows a running average rather than a peak.
        avg = average_value(PARAM_BATT)
        avg_str = "---" if avg is None else format_number(PARAM_BATT, avg)
        peak_text = "AVG {}".format(avg_str)
    else:
        peak_text = "PEAK {}".format(format_peak(current_page))
    if peak_text != last_peak_str:
        peak_label.text = peak_text
        last_peak_str = peak_text

    if stale != last_warning_shown:
        warning_label.text = "NO CAN" if stale else ""
        last_warning_shown = stale

    if is_rpm:
        update_segment_bar(
            shift_bar_palettes, 0 if stale else live_data[PARAM_RPM]["value"],
            0, SHIFT_BAR_MAX_RPM, rpm_alert_color,
        )
    elif is_clt:
        update_clt_bar(clt_bar_bitmap, clt_bar_palette)
    elif is_boost:
        update_boost_bar(boost_bar_bitmap, boost_bar_palette)
    elif is_mat:
        update_mat_bar(mat_bar_bitmap, mat_bar_palette)
    elif is_tps:
        update_tps_bar(tps_bar_bitmap, tps_bar_palette)
    elif is_afr:
        update_afr_bar(afr_bar_bitmap, afr_bar_palette, afr_low_marker, afr_high_marker)
    elif is_map:
        update_map_bar(map_bar_bitmap, map_bar_palette)
    elif is_batt:
        update_batt_bar(batt_bar_bitmap, batt_bar_palette, batt_avg_marker)


# ============================================================================
#  Main loop
# ============================================================================
while True:
    poll_can()          # never blocks - listener timeout=0
    handle_touch()      # single non-blocking touch sample per iteration
    update_display()
    poll_datalogger()   # no-op unless logging is active; rate-limited internally
