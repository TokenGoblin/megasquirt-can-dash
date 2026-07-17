# ============================================================================
#  ui.py - all displayio rendering: pages, bars, markers, banners
# ============================================================================
#  Purpose:   Owns the ILI9341 display and every widget on it. Builds the
#             entire UI ONCE at startup as persistent displayio Groups, then
#             mutates only text/colors/bitmap regions - zero widget
#             construction after init. Renders on a fixed ~20 Hz tick with
#             manual display.refresh(), fully decoupled from CAN arrival.
#  Talks to:  ILI9341 TFT over SPI (via displayio/fourwire), fed by the
#             shared EcuData store from canbus.py.
#  Fits in:   code.py constructs DashUI once, then calls change_page() on
#             touch events and update(ecu, now, bus_ok, log_state) per loop.
#
#  Rendering strategy (the performance-critical decisions, in one place):
#    * display.auto_refresh = False + display.refresh() on our own tick -
#      SPI traffic happens exactly when we decide, at a steady 20 Hz, not
#      whenever displayio feels like it and never mid-update (no tearing).
#    * bitmap_label.Label instead of label.Label for ALL text - each label
#      renders into a single bitmap/TileGrid (one dirty region per change)
#      instead of one TileGrid per glyph, minimizing both RAM and the SPI
#      rectangles pushed per refresh.
#    * Every dynamic element is change-gated on the RAW x10 integer value:
#      nothing is formatted, recolored, or redrawn unless the underlying
#      int actually changed since the last paint. A steady idle screen
#      costs near-zero SPI traffic and zero heap allocation.
#    * Page switching only flips .hidden flags on prebuilt Groups and
#      resets the shared-label caches. Hidden widgets keep their contents,
#      so returning to a page repaints only what changed while it was away.
# ============================================================================

import time

import board
import displayio
import fourwire
import terminalio
import bitmaptools
import adafruit_ili9341
from adafruit_display_text import bitmap_label

import config
import ticks
from canbus import format_x10
from datalog import STATE_NO_SD, STATE_ON

# ==== COLOR GRADIENT HELPERS ================================================

def _lerp_color(color_a, color_b, frac):
    """Blend two 0xRRGGBB colors; frac clamped to 0..1. Returns int color."""
    if frac < 0:
        frac = 0.0
    elif frac > 1:
        frac = 1.0
    ar, ag, ab = (color_a >> 16) & 0xFF, (color_a >> 8) & 0xFF, color_a & 0xFF
    br, bg, bb = (color_b >> 16) & 0xFF, (color_b >> 8) & 0xFF, color_b & 0xFF
    r = int(ar + (br - ar) * frac)
    g = int(ag + (bg - ag) * frac)
    b = int(ab + (bb - ab) * frac)
    return (r << 16) | (g << 8) | b


def gradient_color(value, stops):
    """Color for `value` (real units, float) from a (value, color) stop list.

    Flat color outside the first/last stop, smooth blend between adjacent
    stops; repeating a color at two stops makes a flat plateau. Only called
    on the render tick when a value changed - a few float ops, no allocation
    beyond the returned small int.
    """
    if value <= stops[0][0]:
        return stops[0][1]
    for i in range(1, len(stops)):
        v0, c0 = stops[i - 1]
        v1, c1 = stops[i]
        if value <= v1:
            return _lerp_color(c0, c1, (value - v0) / (v1 - v0))
    return stops[-1][1]


# Per-parameter stop tables, indexed by config.PARAM_* order
# (RPM, MAP, BOOST, CLT, TPS, AFR, BATT, MAT).
_STOPS_BY_PARAM = (
    config.RPM_STOPS,
    config.MAP_STOPS,
    config.BOOST_STOPS,
    config.CLT_STOPS,
    config.TPS_STOPS,
    config.AFR_STOPS,
    config.BATT_STOPS,
    config.MAT_STOPS,
)


def param_color(param, v_x10):
    """Alert color for a channel's x10 value (one float divide + gradient)."""
    return gradient_color(v_x10 / 10.0, _STOPS_BY_PARAM[param])


# ==== SHARED GEOMETRY (derived once from config) ============================

# Continuous bars share the round-LED bar's vertical slot so every page's
# graphic lines up.
_INLINE_X = (config.SCREEN_W - config.INLINE_BAR_W) // 2
_INLINE_Y = config.BAR_Y - config.BAR_SEGMENT_H // 2

# Marker draw modes for _FillBar.
_MARKER_PEAK = 0   # orange line at the power-on peak, drawn into the bitmap
_MARKER_AVG = 1    # orange line at the running average, its own TileGrid


def _make_label(font_scale, color, anchor, position, text=""):
    """One-line bitmap_label factory (all text uses the same builtin font)."""
    return bitmap_label.Label(
        terminalio.FONT, text=text, color=color, scale=font_scale,
        anchor_point=anchor, anchored_position=position,
    )


def _bordered_fill_bar(parent, x, y, w, h):
    """Build the standard bar visual: border rect behind a 3-color fill
    bitmap (0=background, 1=fill, 2=peak marker). Returns (bitmap, palette).
    Startup-only construction."""
    border = displayio.Bitmap(w + 2 * config.BEAM_BORDER_PX, h + 2 * config.BEAM_BORDER_PX, 1)
    border_pal = displayio.Palette(1)
    border_pal[0] = config.BEAM_BORDER_COLOR
    parent.append(displayio.TileGrid(
        border, pixel_shader=border_pal,
        x=x - config.BEAM_BORDER_PX, y=y - config.BEAM_BORDER_PX,
    ))
    fill = displayio.Bitmap(w, h, 3)
    pal = displayio.Palette(3)
    pal[0] = config.COLOR_BG
    pal[1] = config.COLOR_VALUE
    pal[2] = config.BEAM_PEAK_MARKER_COLOR
    parent.append(displayio.TileGrid(fill, pixel_shader=pal, x=x, y=y))
    return fill, pal


def _marker_tilegrid(parent, width, x, y):
    """Small solid-orange marker bar (own TileGrid so moving it repaints a
    few pixels instead of the whole bar - and it can't flicker, because the
    bar's fill redraw never touches it). Starts hidden."""
    bmp = displayio.Bitmap(width, config.INLINE_BAR_H, 1)
    pal = displayio.Palette(1)
    pal[0] = config.BEAM_PEAK_MARKER_COLOR
    tg = displayio.TileGrid(bmp, pixel_shader=pal, x=x, y=y)
    tg.hidden = True
    parent.append(tg)
    return tg


# ==== FILL BAR (MAP / Boost / Coolant / TPS / MAT / Battery) ================

class _FillBar:
    """Left-to-right continuous fill bar with gradient color + hold marker.

    marker_mode selects what the orange line means: _MARKER_PEAK (highest
    since power-on, drawn into the bitmap like the original) or _MARKER_AVG
    (running average, a separate TileGrid - used by Battery).

    update() is fully change-gated: the bitmap is only rewritten when the
    fill length or marker position moved by >= 1 px, and the palette only
    when the gradient color's integer value changed.
    """

    def __init__(self, root, param, vmin, vmax, marker_mode):
        self.param = param
        self.group = displayio.Group()
        self.bitmap, self.palette = _bordered_fill_bar(
            self.group, _INLINE_X, _INLINE_Y, config.INLINE_BAR_W, config.INLINE_BAR_H
        )
        self._marker_mode = marker_mode
        self._avg_tg = None
        if marker_mode == _MARKER_AVG:
            self._avg_tg = _marker_tilegrid(
                self.group, config.BATT_AVG_MARKER_W, _INLINE_X, _INLINE_Y
            )
        # Fixed-point range ends: all px math below is pure int.
        self._min_x10 = int(vmin * 10)
        self._span_x10 = int(vmax * 10) - self._min_x10
        # Render caches (None = never painted, forces first draw).
        self._c_fill = None
        self._c_color = None
        self._c_mark = None
        self.group.hidden = True
        root.append(self.group)

    def _px(self, v_x10):
        """Map an x10 value onto 0..BAR_W pixels, clamped."""
        px = (v_x10 - self._min_x10) * config.INLINE_BAR_W // self._span_x10
        if px < 0:
            return 0
        if px > config.INLINE_BAR_W:
            return config.INLINE_BAR_W
        return px

    def update(self, ecu, now):
        """Repaint fill/marker if (and only if) their pixels changed."""
        w = config.INLINE_BAR_W
        h = config.INLINE_BAR_H

        if ecu.is_stale(self.param, now):
            fill_px = 0            # stale = empty bar (original behavior)
            color = self._c_color  # keep last color; nothing lit anyway
        else:
            v = ecu.value_x10[self.param]
            fill_px = self._px(v)
            color = param_color(self.param, v)

        # Marker position in px (peak into bitmap, average via TileGrid).
        if self._marker_mode == _MARKER_PEAK:
            pk = ecu.peak_x10[self.param]
            mark_px = -1 if pk is None else self._px(pk)
        else:
            avg = ecu.average_x10(self.param)
            mark_px = -1 if avg is None else self._px(avg)

        if fill_px == self._c_fill and color == self._c_color and mark_px == self._c_mark:
            return  # steady state: not one byte of SPI traffic

        if color is not None and color != self._c_color:
            self.palette[1] = color   # palette write repaints the bar once
            self._c_color = color

        if fill_px != self._c_fill or (
            self._marker_mode == _MARKER_PEAK and mark_px != self._c_mark
        ):
            # Repaint fill + background; in-bitmap peak marker goes on top.
            if fill_px > 0:
                bitmaptools.fill_region(self.bitmap, 0, 0, fill_px, h, 1)
            if fill_px < w:
                bitmaptools.fill_region(self.bitmap, fill_px, 0, w, h, 0)
            if self._marker_mode == _MARKER_PEAK and mark_px >= 0:
                m0 = mark_px
                m1 = m0 + config.BEAM_PEAK_MARKER_W
                if m1 > w:
                    m1 = w
                    m0 = m1 - config.BEAM_PEAK_MARKER_W
                    if m0 < 0:
                        m0 = 0
                bitmaptools.fill_region(self.bitmap, m0, 0, m1, h, 2)
            self._c_fill = fill_px

        if self._marker_mode == _MARKER_AVG and mark_px != self._c_mark:
            # Slide the average TileGrid; never redrawn, so it can't blink.
            if mark_px < 0:
                self._avg_tg.hidden = True
            else:
                lo = mark_px - config.BATT_AVG_MARKER_W // 2
                if lo < 0:
                    lo = 0
                elif lo > config.INLINE_BAR_W - config.BATT_AVG_MARKER_W:
                    lo = config.INLINE_BAR_W - config.BATT_AVG_MARKER_W
                self._avg_tg.x = _INLINE_X + lo
                if self._avg_tg.hidden:
                    self._avg_tg.hidden = False
        self._c_mark = mark_px


# ==== AFR CENTER-ZERO BAR ===================================================

class _AfrBar:
    """AFR bar: fill grows from center (14.7 stoich) outward toward the
    current AFR - left when rich, right when lean - with a fixed center tick
    and two TileGrid hold markers (richest LO / leanest HI since power-on).

    Bitmap palette: 0=background, 1=center tick, 2=fill (gradient-colored).
    The markers are separate TileGrids, repositioned only on a new extreme,
    so they hold rock-steady while the fill animates under them.
    """

    def __init__(self, root):
        self.group = displayio.Group()
        self.bitmap, self.palette = _bordered_fill_bar(
            self.group, _INLINE_X, _INLINE_Y, config.INLINE_BAR_W, config.INLINE_BAR_H
        )
        self.palette[1] = config.AFR_CENTER_TICK_COLOR  # slot 1 = tick, not white
        self._low_tg = _marker_tilegrid(
            self.group, config.AFR_PEAK_MARKER_W, _INLINE_X, _INLINE_Y
        )
        self._high_tg = _marker_tilegrid(
            self.group, config.AFR_PEAK_MARKER_W, _INLINE_X, _INLINE_Y
        )
        self._c_pos = None     # cached fill endpoint px (-1 = stale/empty)
        self._c_color = None
        self._c_low = None     # cached marker px positions
        self._c_high = None
        self.group.hidden = True
        root.append(self.group)

    @staticmethod
    def _beam_x(afr):
        """Map AFR (float) to a bar x: MIN..CENTER spans the left half,
        CENTER..MAX the right half, so stoich sits at the exact center pixel
        even though the two sides cover different AFR spans (5.7 vs 3.3)."""
        half = config.INLINE_BAR_W / 2
        if afr <= config.AFR_BAR_CENTER:
            frac = (afr - config.AFR_BAR_MIN) / (config.AFR_BAR_CENTER - config.AFR_BAR_MIN)
            if frac < 0:
                frac = 0.0
            elif frac > 1:
                frac = 1.0
            return int(frac * half)
        frac = (afr - config.AFR_BAR_CENTER) / (config.AFR_BAR_MAX - config.AFR_BAR_CENTER)
        if frac < 0:
            frac = 0.0
        elif frac > 1:
            frac = 1.0
        return int(half + frac * half)

    def _move_marker(self, tg, v_x10, cached):
        """Reposition one hold marker; returns the new cache value. Only
        touches the TileGrid when the pixel actually changes."""
        if v_x10 is None:
            if not tg.hidden:
                tg.hidden = True
            return None
        px = self._beam_x(v_x10 / 10.0)
        if px == cached:
            return cached
        lo = px - config.AFR_PEAK_MARKER_W // 2
        if lo < 0:
            lo = 0
        elif lo > config.INLINE_BAR_W - config.AFR_PEAK_MARKER_W:
            lo = config.INLINE_BAR_W - config.AFR_PEAK_MARKER_W
        tg.x = _INLINE_X + lo
        if tg.hidden:
            tg.hidden = False
        return px

    def update(self, ecu, now):
        """Repaint the center-out fill when the endpoint or color changed;
        always give the (self-gating) markers a chance to move."""
        w = config.INLINE_BAR_W
        h = config.INLINE_BAR_H
        center = w // 2

        if ecu.is_stale(config.PARAM_AFR, now):
            pos = -1           # sentinel: empty track
            color = self._c_color
        else:
            v = ecu.value_x10[config.PARAM_AFR]
            pos = self._beam_x(v / 10.0)
            color = param_color(config.PARAM_AFR, v)

        if pos != self._c_pos or color != self._c_color:
            if color is not None and color != self._c_color:
                self.palette[2] = color
                self._c_color = color
            if pos < 0:
                bitmaptools.fill_region(self.bitmap, 0, 0, w, h, 0)
            else:
                # Background both sides, fill between center and pos - every
                # pixel written exactly once, so the fill can't flash.
                lo, hi = (pos, center) if pos < center else (center, pos)
                if lo > 0:
                    bitmaptools.fill_region(self.bitmap, 0, 0, lo, h, 0)
                if hi < w:
                    bitmaptools.fill_region(self.bitmap, hi, 0, w, h, 0)
                if hi > lo:
                    bitmaptools.fill_region(self.bitmap, lo, 0, hi, h, 2)
            # Fixed stoich reference tick, drawn last so it stays visible.
            t0 = center - config.AFR_CENTER_TICK_W // 2
            if t0 < 0:
                t0 = 0
            t1 = t0 + config.AFR_CENTER_TICK_W
            if t1 > w:
                t1 = w
            bitmaptools.fill_region(self.bitmap, t0, 0, t1, h, 1)
            self._c_pos = pos

        self._c_low = self._move_marker(self._low_tg, ecu.low_x10[config.PARAM_AFR], self._c_low)
        self._c_high = self._move_marker(self._high_tg, ecu.peak_x10[config.PARAM_AFR], self._c_high)


# ==== RPM SHIFT-LIGHT BAR ===================================================

class _SegmentBar:
    """Race-style row of round LED 'bulbs' for the RPM page.

    Each bulb is a filled circle drawn ONCE at startup into its own 2-color
    bitmap (0 = dark housing, 1 = the bulb); updates only ever assign
    palette[1]. Lit bulbs use the color for the RPM at that bulb's own
    position - precomputed here, since it never changes - so the bar sweeps
    through the gradient like a real shift light as revs climb.
    """

    def __init__(self, root):
        n = config.SHIFT_BAR_SEGMENTS
        self.group = displayio.Group()
        self._palettes = []
        self._cache = [None] * n     # last color int written per bulb
        # Lit color per bulb index is a constant: color at that bulb's RPM.
        span = float(config.SHIFT_BAR_MAX_RPM)
        self._lit_colors = tuple(
            gradient_color(i * (span / n), config.RPM_STOPS) for i in range(n)
        )

        total_w = n * config.BAR_SEGMENT_W + (n - 1) * config.BAR_GAP
        start_x = (config.SCREEN_W - total_w) // 2
        radius = config.BAR_SEGMENT_W // 2 - 1
        cx = config.BAR_SEGMENT_W // 2
        cy = config.BAR_SEGMENT_H // 2
        for i in range(n):
            bmp = displayio.Bitmap(config.BAR_SEGMENT_W, config.BAR_SEGMENT_H, 2)
            self._draw_filled_circle(bmp, cx, cy, radius, 1)
            pal = displayio.Palette(2)
            pal[0] = config.BAR_HOUSING_COLOR
            pal[1] = config.COLOR_DOT_INACTIVE
            self.group.append(displayio.TileGrid(
                bmp, pixel_shader=pal,
                x=start_x + i * (config.BAR_SEGMENT_W + config.BAR_GAP),
                y=config.BAR_Y - config.BAR_SEGMENT_H // 2,
            ))
            self._palettes.append(pal)
        self.group.hidden = True
        root.append(self.group)

    @staticmethod
    def _draw_filled_circle(bmp, cx, cy, radius, value):
        """Per-pixel filled disc. bitmaptools.draw_circle() only plots the
        1 px outline (Bresenham), so a solid bulb needs this manual scan -
        O(r^2) but it runs once per bulb at startup, never in the loop."""
        r2 = radius * radius
        for y in range(cy - radius, cy + radius + 1):
            if y < 0 or y >= bmp.height:
                continue
            dy = y - cy
            for x in range(cx - radius, cx + radius + 1):
                if x < 0 or x >= bmp.width:
                    continue
                dx = x - cx
                if dx * dx + dy * dy <= r2:
                    bmp[x, y] = value

    def update(self, ecu, now):
        """Light 0..N bulbs for the current RPM; write only changed palettes."""
        if ecu.is_stale(config.PARAM_RPM, now):
            lit = 0
        else:
            # x10 int math: rpm*10 * N // (max_rpm*10)
            lit = ecu.value_x10[config.PARAM_RPM] * config.SHIFT_BAR_SEGMENTS // (
                config.SHIFT_BAR_MAX_RPM * 10
            )
            if lit < 0:
                lit = 0
            elif lit > config.SHIFT_BAR_SEGMENTS:
                lit = config.SHIFT_BAR_SEGMENTS
        for i in range(config.SHIFT_BAR_SEGMENTS):
            want = self._lit_colors[i] if i < lit else config.COLOR_DOT_INACTIVE
            if self._cache[i] != want:
                self._palettes[i][1] = want
                self._cache[i] = want


# ==== OVERVIEW GRID PAGE ====================================================

class _Overview:
    """2x2 grid of name+value cells (Boost / Coolant / AFR / Battery).
    Static name labels; value labels change-gated on (stale, raw value)."""

    def __init__(self, root):
        self.group = displayio.Group()
        self._value_labels = []
        self._c_key = [None] * len(config.GRID_PARAMS)    # (stale, v_x10)
        self._c_color = [None] * len(config.GRID_PARAMS)
        for i, param in enumerate(config.GRID_PARAMS):
            self.group.append(_make_label(
                config.GRID_NAME_SCALE, config.COLOR_UNITS,
                (0.5, 0.5), config.GRID_NAME_POS[i], text=config.PAGES[param][0],
            ))
            lbl = _make_label(
                config.GRID_VALUE_SCALE, config.COLOR_VALUE,
                (0.5, 0.5), config.GRID_VALUE_POS[i],
            )
            self.group.append(lbl)
            self._value_labels.append(lbl)
        self.group.hidden = True
        root.append(self.group)

    def update(self, ecu, now):
        """Refresh any cell whose value or staleness changed."""
        for i, param in enumerate(config.GRID_PARAMS):
            stale = ecu.is_stale(param, now)
            v = ecu.value_x10[param]
            key = -1000000 if stale else v   # single int cache key
            if key == self._c_key[i]:
                continue
            self._c_key[i] = key
            lbl = self._value_labels[i]
            if stale:
                text = "---"
                color = config.COLOR_VALUE_STALE
            else:
                text = format_x10(v, config.PAGES[param][2])
                color = param_color(param, v)
            if lbl.text != text:
                lbl.text = text
            if color != self._c_color[i]:
                lbl.color = color
                self._c_color[i] = color


# ==== BOOST + MAT BEAM PAGE =================================================

class _BeamGauge:
    """One row of the beam page: name label, big value, wide fill bar with
    in-bitmap peak marker. Same change-gating as _FillBar."""

    def __init__(self, group, param, name, text_y, bar_y, vmin, vmax):
        self.param = param
        group.append(_make_label(
            config.BEAM_LABEL_SCALE, config.COLOR_UNITS,
            (0.0, 0.5), ((config.SCREEN_W - config.BEAM_BAR_W) // 2, text_y), text=name,
        ))
        self.value_label = _make_label(
            config.BEAM_VALUE_SCALE, config.COLOR_VALUE,
            (1.0, 0.5),
            ((config.SCREEN_W - config.BEAM_BAR_W) // 2 + config.BEAM_BAR_W, text_y),
        )
        group.append(self.value_label)
        self.bitmap, self.palette = _bordered_fill_bar(
            group, (config.SCREEN_W - config.BEAM_BAR_W) // 2, bar_y,
            config.BEAM_BAR_W, config.BEAM_BAR_H,
        )
        self._min_x10 = int(vmin * 10)
        self._span_x10 = int(vmax * 10) - self._min_x10
        self._c_key = None
        self._c_color = None
        self._c_fill = None
        self._c_mark = None
        self._c_lbl_color = None

    def _px(self, v_x10, w):
        px = (v_x10 - self._min_x10) * w // self._span_x10
        if px < 0:
            return 0
        if px > w:
            return w
        return px

    def update(self, ecu, now):
        w = config.BEAM_BAR_W
        h = config.BEAM_BAR_H
        stale = ecu.is_stale(self.param, now)
        v = ecu.value_x10[self.param]
        key = -1000000 if stale else v

        # Value text + its color (red when stale).
        if key != self._c_key:
            self._c_key = key
            text = "---" if stale else format_x10(v, config.PAGES[self.param][2])
            if self.value_label.text != text:
                self.value_label.text = text
            lbl_color = config.COLOR_VALUE_STALE if stale else config.COLOR_VALUE
            if lbl_color != self._c_lbl_color:
                self.value_label.color = lbl_color
                self._c_lbl_color = lbl_color

        fill_px = 0 if stale else self._px(v, w)
        color = self._c_color if stale else param_color(self.param, v)
        pk = ecu.peak_x10[self.param]
        mark_px = -1 if pk is None else self._px(pk, w)

        if fill_px == self._c_fill and color == self._c_color and mark_px == self._c_mark:
            return
        if color is not None and color != self._c_color:
            self.palette[1] = color
            self._c_color = color
        if fill_px > 0:
            bitmaptools.fill_region(self.bitmap, 0, 0, fill_px, h, 1)
        if fill_px < w:
            bitmaptools.fill_region(self.bitmap, fill_px, 0, w, h, 0)
        if mark_px >= 0:
            m0 = mark_px
            m1 = m0 + config.BEAM_PEAK_MARKER_W
            if m1 > w:
                m1 = w
                m0 = m1 - config.BEAM_PEAK_MARKER_W
                if m0 < 0:
                    m0 = 0
            bitmaptools.fill_region(self.bitmap, m0, 0, m1, h, 2)
        self._c_fill = fill_px
        self._c_mark = mark_px


# ==== TOP-LEVEL UI ==========================================================

class DashUI:
    """The whole screen. Construct once; then only change_page() + update().

    Construction shows the boot splash (the one place time.sleep() is used -
    the main loop hasn't started yet), then builds every page's widgets into
    one persistent root Group. Nothing is constructed after __init__.
    """

    def __init__(self):
        displayio.release_displays()
        spi = board.SPI()
        bus = fourwire.FourWire(
            spi, command=config.TFT_DC_PIN, chip_select=config.TFT_CS_PIN,
            reset=config.TFT_RST_PIN,
            baudrate=config.TFT_BAUDRATE,   # fastest stable SPI clock for this panel
        )
        self.display = adafruit_ili9341.ILI9341(
            bus, width=config.SCREEN_W, height=config.SCREEN_H,
            rotation=config.DISPLAY_ROTATION,
        )
        # Manual refresh from here on: WE decide when SPI traffic happens.
        self.display.auto_refresh = False

        self._show_splash()

        # ---- build all static chrome + dynamic widgets, once --------------
        root = displayio.Group()

        bg = displayio.Bitmap(config.SCREEN_W, config.SCREEN_H, 1)
        bg_pal = displayio.Palette(1)
        bg_pal[0] = config.COLOR_BG
        root.append(displayio.TileGrid(bg, pixel_shader=bg_pal, x=0, y=0))

        cx = config.SCREEN_W // 2
        self.title_label = _make_label(
            config.TITLE_SCALE, config.COLOR_TITLE, (0.5, 0.5), (cx, config.TITLE_CENTER_Y))
        root.append(self.title_label)
        self.value_label = _make_label(
            config.VALUE_SCALE, config.COLOR_VALUE, (0.5, 0.5), (cx, config.VALUE_CENTER_Y))
        root.append(self.value_label)
        self.units_label = _make_label(
            config.UNITS_SCALE, config.COLOR_UNITS, (0.5, 0.5), (cx, config.UNITS_CENTER_Y))
        root.append(self.units_label)
        self.peak_label = _make_label(
            config.PEAK_SCALE, config.COLOR_PEAK, (0.5, 0.5), (cx, config.PEAK_CENTER_Y))
        root.append(self.peak_label)
        # NO CAN banner (top-right) + datalog state (top-left).
        self.warning_label = _make_label(
            1, config.COLOR_WARNING, (1.0, 0.0), (config.SCREEN_W - 4, 4))
        root.append(self.warning_label)
        self.datalog_label = _make_label(
            1, config.COLOR_DOT_INACTIVE, (0.0, 0.0), (4, 4))
        root.append(self.datalog_label)
        # Dim tap-zone hints.
        root.append(_make_label(
            config.ARROW_SCALE, config.COLOR_ARROW, (0.0, 0.5),
            (6, config.VALUE_CENTER_Y), text="<"))
        root.append(_make_label(
            config.ARROW_SCALE, config.COLOR_ARROW, (1.0, 0.5),
            (config.SCREEN_W - 6, config.VALUE_CENTER_Y), text=">"))

        # Per-page graphics. Only one is visible at a time; switching pages
        # just flips .hidden flags on these prebuilt groups.
        self._shift_bar = _SegmentBar(root)
        self._overview = _Overview(root)
        self._beam_boost = None   # built inside the beam group below
        beam_group = displayio.Group()
        self._beam_boost = _BeamGauge(
            beam_group, config.PARAM_BOOST, "BOOST",
            config.BEAM_TOP_TEXT_Y, config.BEAM_TOP_BAR_Y,
            config.BOOST_MIN_PSI, config.BOOST_MAX_PSI,
        )
        self._beam_mat = _BeamGauge(
            beam_group, config.PARAM_MAT, "MAT",
            config.BEAM_BOT_TEXT_Y, config.BEAM_BOT_BAR_Y,
            config.MAT_BAR_MIN_F, config.MAT_BAR_MAX_F,
        )
        beam_group.hidden = True
        root.append(beam_group)
        self._beam_group = beam_group

        self._afr_bar = _AfrBar(root)
        self._fill_bars = {
            config.PARAM_MAP: _FillBar(
                root, config.PARAM_MAP, config.MAP_BAR_MIN, config.MAP_BAR_MAX, _MARKER_PEAK),
            config.PARAM_BATT: _FillBar(
                root, config.PARAM_BATT, config.BATT_BAR_MIN, config.BATT_BAR_MAX, _MARKER_AVG),
            config.PARAM_BOOST: _FillBar(
                root, config.PARAM_BOOST, config.BOOST_MIN_PSI, config.BOOST_MAX_PSI, _MARKER_PEAK),
            config.PARAM_CLT: _FillBar(
                root, config.PARAM_CLT, config.CLT_BAR_MIN_F, config.CLT_BAR_MAX_F, _MARKER_PEAK),
            config.PARAM_TPS: _FillBar(
                root, config.PARAM_TPS, config.TPS_BAR_MIN_PCT, config.TPS_BAR_MAX_PCT, _MARKER_PEAK),
            config.PARAM_MAT: _FillBar(
                root, config.PARAM_MAT, config.MAT_BAR_MIN_F, config.MAT_BAR_MAX_F, _MARKER_PEAK),
        }

        # Page indicator dots - recolored, never rebuilt.
        self._dot_palettes = []
        start_x = (config.SCREEN_W - (config.PAGE_COUNT - 1) * config.DOT_SPACING) // 2
        for i in range(config.PAGE_COUNT):
            pal = displayio.Palette(1)
            pal[0] = config.COLOR_DOT_INACTIVE
            bmp = displayio.Bitmap(config.DOT_SIZE, config.DOT_SIZE, 1)
            root.append(displayio.TileGrid(
                bmp, pixel_shader=pal,
                x=start_x + i * config.DOT_SPACING - config.DOT_SIZE // 2,
                y=config.DOTS_Y - config.DOT_SIZE // 2,
            ))
            self._dot_palettes.append(pal)

        self.display.root_group = root
        self._root = root

        # ---- render state ---------------------------------------------------
        self._page = config.PARAM_RPM
        self._page_dirty = True     # forces chrome layout on first update
        self._last_tick = None      # None = render immediately
        # Shared-label caches (reset on every page change).
        self._c_value_key = None    # (stale?) raw int key for the big number
        self._c_value_text = None
        self._c_value_color = None
        self._c_peak_a = None       # peak/lo/avg cache ints (meaning is per-page)
        self._c_peak_b = None
        self._c_banner = None       # bool: banner currently shown
        self._c_log_state = None    # datalog indicator cache

    # ---- boot splash --------------------------------------------------------

    def _show_splash(self):
        """Show /splash.bmp for the configured time; silently skip if absent.
        Startup-only blocking (time.sleep) - the main loop hasn't begun."""
        try:
            bmp = displayio.OnDiskBitmap(config.SPLASH_IMAGE_PATH)
            grp = displayio.Group()
            grp.append(displayio.TileGrid(bmp, pixel_shader=bmp.pixel_shader))
            self.display.root_group = grp
            self.display.refresh(minimum_frames_per_second=0)
            time.sleep(config.SPLASH_DURATION_S)
        except OSError as e:
            print("Splash image not found/failed to load:", e)

    # ---- fatal error screen ---------------------------------------------------

    def show_fatal(self, message):
        """Full red screen + message, then halt forever. For unrecoverable
        startup failures (e.g. CAN peripheral init) where the dash is
        useless anyway. Never returns."""
        err_bmp = displayio.Bitmap(config.SCREEN_W, config.SCREEN_H, 1)
        err_pal = displayio.Palette(1)
        err_pal[0] = 0xFF0000
        grp = displayio.Group()
        grp.append(displayio.TileGrid(err_bmp, pixel_shader=err_pal, x=0, y=0))
        grp.append(_make_label(
            2, 0xFFFFFF, (0.5, 0.5),
            (config.SCREEN_W // 2, config.SCREEN_H // 2), text=message))
        self.display.root_group = grp
        self.display.refresh(minimum_frames_per_second=0)
        print(message)
        while True:
            pass

    # ---- page navigation --------------------------------------------------------

    def change_page(self, delta):
        """Step `delta` pages (wraps). Cheap: flags the chrome dirty; the
        actual relayout happens on the next render tick (<= UI_TICK_MS)."""
        self._page = (self._page + delta) % config.PAGE_COUNT
        self._page_dirty = True

    def _apply_page_chrome(self):
        """Show/hide the prebuilt groups for the new page and reset the
        shared-label caches. No widget construction - .hidden flips only."""
        page = self._page
        is_overview = page == config.OVERVIEW_PAGE
        is_beam = page == config.BEAM_PAGE
        is_special = is_overview or is_beam

        self.title_label.text = "" if is_special else config.PAGES[page][0]
        self.value_label.hidden = is_special
        self.units_label.hidden = is_special
        self.peak_label.hidden = is_special
        if not is_special:
            self.units_label.text = config.PAGES[page][1]

        self._overview.group.hidden = not is_overview
        self._beam_group.hidden = not is_beam
        self._shift_bar.group.hidden = page != config.PARAM_RPM
        self._afr_bar.group.hidden = page != config.PARAM_AFR
        for param, bar in self._fill_bars.items():
            bar.group.hidden = page != param

        # Page changes are rare user events - just recolor all dots.
        for i, pal in enumerate(self._dot_palettes):
            pal[0] = config.COLOR_DOT_ACTIVE if i == page else config.COLOR_DOT_INACTIVE

        # Invalidate the shared-label caches so the new page paints fully.
        self._c_value_key = None
        self._c_value_text = None
        self._c_value_color = None
        self._c_peak_a = None
        self._c_peak_b = None
        self._c_banner = None
        if is_special:
            # Special pages show staleness per-cell; the corner banner is
            # single-gauge-only, so clear it on entry.
            self.warning_label.text = ""
        self._page_dirty = False

    # ---- render tick ---------------------------------------------------------------

    def update(self, ecu, now, bus_ok, log_state):
        """Render pass, self-gated to the UI tick (~20 Hz), ending in a
        manual display.refresh().

        Parameters:
            ecu       - shared EcuData store.
            now       - this loop pass's ticks.ms() stamp.
            bus_ok    - CanBus.bus_ok; False raises the NO CAN banner
                        immediately (on top of per-channel stale timeouts).
            log_state - datalog.STATE_* for the corner indicator.
        Between ticks this returns after one integer compare - the loop
        spends its time in CAN drain, not drawing.
        """
        if self._last_tick is not None and ticks.diff(now, self._last_tick) < config.UI_TICK_MS:
            return
        self._last_tick = now

        if self._page_dirty:
            self._apply_page_chrome()

        # Datalog corner indicator (identical on every page).
        if log_state != self._c_log_state:
            self._c_log_state = log_state
            if log_state == STATE_NO_SD:
                self.datalog_label.text = "NO SD"
                self.datalog_label.color = config.COLOR_WARNING
            elif log_state == STATE_ON:
                self.datalog_label.text = "LOG ON"
                self.datalog_label.color = config.COLOR_ALERT_GREEN
            else:  # STATE_STANDBY
                self.datalog_label.text = "LOG OFF"
                self.datalog_label.color = config.COLOR_DOT_INACTIVE

        page = self._page
        if page == config.OVERVIEW_PAGE:
            self._overview.update(ecu, now)
        elif page == config.BEAM_PAGE:
            self._beam_boost.update(ecu, now)
            self._beam_mat.update(ecu, now)
        else:
            self._render_single_gauge(ecu, now, bus_ok, page)

        # One SPI push per tick, covering every region dirtied above.
        self.display.refresh(minimum_frames_per_second=0)

    def _render_single_gauge(self, ecu, now, bus_ok, page):
        """Big number + peak line + NO CAN banner + this page's bar."""
        stale = ecu.is_stale(page, now)
        v = ecu.value_x10[page]
        key = -1000000 if stale else v

        # -- big value: format/recolor only when the raw int changed --------
        if key != self._c_value_key:
            self._c_value_key = key
            if stale:
                text = "---"
                color = config.COLOR_VALUE_STALE
            else:
                text = format_x10(v, config.PAGES[page][2])
                color = param_color(page, v)
            if text != self._c_value_text:
                self.value_label.text = text
                self._c_value_text = text
            if color != self._c_value_color:
                self.value_label.color = color
                self._c_value_color = color

        # -- peak line: PEAK on most pages, LO/HI on AFR, AVG on Battery ----
        if page == config.PARAM_AFR:
            a = ecu.low_x10[page]
            b = ecu.peak_x10[page]
            if a != self._c_peak_a or b != self._c_peak_b:
                self._c_peak_a = a
                self._c_peak_b = b
                self.peak_label.text = "LO {}  HI {}".format(
                    format_x10(a, 1), format_x10(b, 1))
        elif page == config.PARAM_BATT:
            a = ecu.average_x10(page)
            if a != self._c_peak_a:
                self._c_peak_a = a
                self.peak_label.text = "AVG {}".format(format_x10(a, 1))
        else:
            a = ecu.peak_x10[page]
            if a != self._c_peak_a:
                self._c_peak_a = a
                self.peak_label.text = "PEAK {}".format(
                    format_x10(a, config.PAGES[page][2]))

        # -- NO CAN banner: this channel stale, or the controller itself in
        #    a failed bus state (bus-off shows instantly, before timeouts) --
        banner = stale or not bus_ok
        if banner != self._c_banner:
            self.warning_label.text = "NO CAN" if banner else ""
            self._c_banner = banner

        # -- this page's bar graphic ----------------------------------------
        if page == config.PARAM_RPM:
            self._shift_bar.update(ecu, now)
        elif page == config.PARAM_AFR:
            self._afr_bar.update(ecu, now)
        else:
            bar = self._fill_bars.get(page)
            if bar is not None:
                bar.update(ecu, now)
