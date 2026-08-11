# Tests for the pure geometry and color maths in ui.py. No displayio object is
# constructed - the bar classes' _px helpers are called against a minimal
# stand-in carrying just the two range attributes they read.

import unittest

import stubs

stubs.install()   # CircuitPython stand-ins + repo path; before any dash import

import canbus
import config
import ui


class _Range:
    """Stands in for a bar instance: only _min_x10/_span_x10 are read."""

    def __init__(self, vmin, vmax):
        self._min_x10 = int(vmin * 10)
        self._span_x10 = int(vmax * 10) - self._min_x10


class TestFillBarPixels(unittest.TestCase):

    def px(self, vmin, vmax, value):
        return ui._FillBar._px(_Range(vmin, vmax), int(value * 10))

    def test_ends_and_midpoint(self):
        w = config.INLINE_BAR_W
        self.assertEqual(self.px(0, 100, 0), 0)
        self.assertEqual(self.px(0, 100, 100), w)
        self.assertEqual(self.px(0, 100, 50), w // 2)

    def test_clamps_outside_the_range(self):
        w = config.INLINE_BAR_W
        self.assertEqual(self.px(100, 250, 40), 0)      # cold engine, below min
        self.assertEqual(self.px(100, 250, 400), w)     # sensor fault, above max
        self.assertEqual(self.px(0, 100, -50), 0)

    def test_never_escapes_the_bitmap_over_the_full_int16_domain(self):
        """A corrupt frame must clamp, never index outside the bar - writing
        past a Bitmap's width is how a display driver crashes."""
        for vmin, vmax in ((config.CLT_BAR_MIN_F, config.CLT_BAR_MAX_F),
                           (config.MAT_BAR_MIN_F, config.MAT_BAR_MAX_F),
                           (config.MAP_BAR_MIN, config.MAP_BAR_MAX),
                           (config.BATT_BAR_MIN, config.BATT_BAR_MAX),
                           (config.BOOST_MIN_PSI, config.BOOST_MAX_PSI)):
            rng = _Range(vmin, vmax)
            for raw in range(-32768, 32768, 37):
                px = ui._FillBar._px(rng, raw)
                self.assertGreaterEqual(px, 0)
                self.assertLessEqual(px, config.INLINE_BAR_W)

    def test_beam_gauge_px_matches_its_own_width(self):
        rng = _Range(config.BOOST_MIN_PSI, config.BOOST_MAX_PSI)
        w = config.BEAM_BAR_W
        self.assertEqual(ui._BeamGauge._px(rng, 0, w), 0)
        self.assertEqual(ui._BeamGauge._px(rng, int(config.BOOST_MAX_PSI * 10), w), w)
        for raw in range(-32768, 32768, 53):
            self.assertTrue(0 <= ui._BeamGauge._px(rng, raw, w) <= w)


class TestAfrBar(unittest.TestCase):
    """The AFR bar is center-zero: stoich must sit on the exact center pixel
    even though rich and lean cover different AFR spans."""

    def test_stoich_lands_dead_center(self):
        self.assertEqual(ui._AfrBar._beam_x(config.AFR_BAR_CENTER),
                         config.INLINE_BAR_W // 2)

    def test_ends_map_to_the_bar_ends(self):
        self.assertEqual(ui._AfrBar._beam_x(config.AFR_BAR_MIN), 0)
        self.assertEqual(ui._AfrBar._beam_x(config.AFR_BAR_MAX), config.INLINE_BAR_W)

    def test_rich_is_left_of_center_and_lean_is_right(self):
        center = config.INLINE_BAR_W // 2
        self.assertLess(ui._AfrBar._beam_x(12.0), center)
        self.assertGreater(ui._AfrBar._beam_x(16.0), center)

    def test_monotonic_and_bounded_over_everything_a_byte_can_hold(self):
        """AFR arrives as a single unsigned byte, so 0.0-25.5 is the whole
        domain and all of it has to stay on the bar."""
        last = -1
        for raw in range(0, 256):
            px = ui._AfrBar._beam_x(raw / 10.0)
            self.assertTrue(0 <= px <= config.INLINE_BAR_W)
            self.assertGreaterEqual(px, last)
            last = px


class TestGradients(unittest.TestCase):

    def test_flat_outside_the_end_stops(self):
        stops = ((10.0, 0x00FF00), (20.0, 0xFF0000))
        self.assertEqual(ui.gradient_color(-5.0, stops), 0x00FF00)
        self.assertEqual(ui.gradient_color(999.0, stops), 0xFF0000)

    def test_endpoints_are_exact(self):
        stops = ((10.0, 0x00FF00), (20.0, 0xFF0000))
        self.assertEqual(ui.gradient_color(10.0, stops), 0x00FF00)
        self.assertEqual(ui.gradient_color(20.0, stops), 0xFF0000)

    def test_blends_between_stops(self):
        stops = ((0.0, 0x000000), (10.0, 0xFFFFFF))
        mid = ui.gradient_color(5.0, stops)
        self.assertEqual(mid, 0x7F7F7F)

    def test_repeated_color_makes_a_plateau(self):
        """Coolant and battery both rely on this to hold a flat 'normal' band.
        For coolant the flat green runs from BLUE_MAX+BLEND to GREEN_MAX-BLEND
        (170-190 F as shipped) - either side of that is mid-blend, not green."""
        lo = config.CLT_BLUE_MAX_F + config.CLT_BLEND_BAND_F
        hi = config.CLT_GREEN_MAX_F - config.CLT_BLEND_BAND_F
        for temp in (lo, (lo + hi) / 2, hi):
            self.assertEqual(ui.gradient_color(temp, config.CLT_STOPS),
                             config.COLOR_ALERT_GREEN)
        # And the shipped band really does cover a normal running temperature.
        self.assertLessEqual(lo, 180.0)
        self.assertGreaterEqual(hi, 180.0)

    def test_battery_plateaus_cover_resting_and_charging(self):
        """Two flat bands: a resting battery and a charging one read very
        differently and both are 'normal'."""
        for volts in (config.BATT_COLD_MIN_V, 12.4, config.BATT_COLD_MAX_V):
            self.assertEqual(ui.gradient_color(volts, config.BATT_STOPS),
                             config.COLOR_ALERT_BLUE)
        for volts in (config.BATT_RUN_MIN_V, 13.8, config.BATT_RUN_MAX_V):
            self.assertEqual(ui.gradient_color(volts, config.BATT_STOPS),
                             config.COLOR_ALERT_GREEN)
        self.assertEqual(ui.gradient_color(10.0, config.BATT_STOPS),
                         config.COLOR_ALERT_RED)     # flat battery
        self.assertEqual(ui.gradient_color(16.0, config.BATT_STOPS),
                         config.COLOR_ALERT_RED)     # overcharging

    def test_every_channel_stays_inside_24_bit_color(self):
        for stops in ui._STOPS_BY_PARAM:
            for raw in range(-3000, 7000, 13):
                color = ui.gradient_color(raw / 10.0, stops)
                self.assertTrue(0 <= color <= 0xFFFFFF)

    def test_map_stops_follow_the_live_baro_reference(self):
        try:
            ui.set_map_baro(84.1)
            self.assertEqual(ui.gradient_color(70.0, ui._MAP_STOPS_LIVE),
                             config.COLOR_ALERT_BLUE)      # vacuum
            self.assertEqual(ui.gradient_color(120.0, ui._MAP_STOPS_LIVE),
                             config.COLOR_ALERT_GREEN)     # boost
            # The boundary tracks the reference, not sea level: 95 kPa is
            # boost at 5,000 ft even though it's vacuum at sea level.
            self.assertEqual(ui.gradient_color(95.0, ui._MAP_STOPS_LIVE),
                             config.COLOR_ALERT_GREEN)
            ui.set_map_baro(101.3)
            self.assertEqual(ui.gradient_color(95.0, ui._MAP_STOPS_LIVE),
                             config.COLOR_ALERT_BLUE)
        finally:
            ui.set_map_baro(config.BARO_FALLBACK_KPA)


class TestTopRowLayout(unittest.TestCase):
    """Four independent status indicators share the top row, each placed with
    a different anchor. They can all be showing at once - a card fault, a dead
    touch chip, a caught fault and an alarm is an unlikely day but a legal
    one - and on a 240 px screen there is not much room to be wrong in.

    This pins the arithmetic so that adding a fifth indicator, renaming a
    channel, or raising MAX_FAULTS_BEFORE_HALT can't quietly overlap two of
    them. Character cell is 6 px wide at scale 1.
    """

    CHAR_W = 6

    def span_left(self, x, text, scale=1):
        return (x, x + len(text) * self.CHAR_W * scale)

    def span_right(self, x, text, scale=1):
        return (x - len(text) * self.CHAR_W * scale, x)

    def span_center(self, x, text, scale=1):
        half = len(text) * self.CHAR_W * scale // 2
        return (x - half, x + half)

    def worst_indicators(self):
        """Widest text each indicator can ever display, with its placement."""
        # Datalog corner: anchor (0,0) at x=4.
        datalog = self.span_left(4, "LOG OFF")
        # Touch status: anchor (0,0) at x=50.
        touch_ind = self.span_left(50, "NO TOUCH")
        # Fault counter: anchor (0.5,0) at screen center. note_fault() only
        # ever runs below the halt threshold, so that bounds the digits.
        worst_faults = "FLT {}".format(config.MAX_FAULTS_BEFORE_HALT - 1)
        fault = self.span_center(config.SCREEN_W // 2, worst_faults)
        # Banner: anchor (1,0) at x=SCREEN_W-4. Worst case is the longest
        # channel name plus the widest value its plausibility range allows.
        widest_banner = "NO CAN"
        for param, (lo, hi) in enumerate(config.SANE_RANGES):
            for bound in (lo, hi):
                candidate = config.PAGES[param][0] + " " + canbus.format_x10(
                    int(bound * 10), config.PAGES[param][2])
                if len(candidate) > len(widest_banner):
                    widest_banner = candidate
        banner = self.span_right(config.SCREEN_W - 4, widest_banner)
        return (("datalog", datalog), ("touch", touch_ind),
                ("fault", fault), ("banner", banner))

    def test_no_two_indicators_overlap(self):
        spans = self.worst_indicators()
        for i in range(len(spans)):
            for j in range(i + 1, len(spans)):
                (name_a, (a0, a1)), (name_b, (b0, b1)) = spans[i], spans[j]
                self.assertTrue(a1 <= b0 or b1 <= a0,
                                "{} ({}-{}) overlaps {} ({}-{})".format(
                                    name_a, a0, a1, name_b, b0, b1))

    def test_all_indicators_stay_on_screen(self):
        for name, (start, end) in self.worst_indicators():
            self.assertGreaterEqual(start, 0, name)
            self.assertLessEqual(end, config.SCREEN_W, name)

    def test_indicators_clear_the_page_title(self):
        """The title sits at TITLE_CENTER_Y at scale 2; the top row is a
        scale-1 strip at y=4, so they must not share rows."""
        top_row_bottom = 4 + 12          # one scale-1 character cell
        title_top = config.TITLE_CENTER_Y - (12 * config.TITLE_SCALE) // 2
        self.assertLessEqual(top_row_bottom, title_top)

    def test_baro_readout_clears_the_peak_line_and_the_dots(self):
        """The Boost page adds a readout between the peak line and the page
        dots - the only place on a single-gauge page with room for it."""
        peak_bottom = config.PEAK_CENTER_Y + (12 * config.PEAK_SCALE) // 2
        baro_top = config.BARO_CENTER_Y - 6
        baro_bottom = config.BARO_CENTER_Y + 6
        dots_top = config.DOTS_Y - config.DOT_SIZE // 2
        self.assertGreaterEqual(baro_top, peak_bottom)
        self.assertLessEqual(baro_bottom, dots_top)

    def test_baro_readout_fits_the_screen_width(self):
        widest = "BARO " + canbus.format_x10(
            int(config.BARO_MAX_KPA * 10), 1) + " EST"
        self.assertLessEqual(len(widest) * self.CHAR_W, config.SCREEN_W)


class TestShiftLightColors(unittest.TestCase):
    """F-13 regression: each bulb is colored for the RPM at which it LIGHTS."""

    def lit_colors(self):
        n = config.SHIFT_BAR_SEGMENTS
        span = float(config.SHIFT_BAR_MAX_RPM)
        return [ui.gradient_color((i + 1) * (span / n), config.RPM_STOPS)
                for i in range(n)]

    def rpm_where_bulb_lights(self, i):
        """Smallest RPM for which ui's own lit-count arithmetic lights bulb i."""
        for rpm in range(0, config.SHIFT_BAR_MAX_RPM + 1):
            lit = rpm * 10 * config.SHIFT_BAR_SEGMENTS // (config.SHIFT_BAR_MAX_RPM * 10)
            if lit > i:
                return rpm
        return config.SHIFT_BAR_MAX_RPM

    def test_each_bulb_shows_the_color_of_its_own_switch_on_rpm(self):
        colors = self.lit_colors()
        for i, color in enumerate(colors):
            expected = ui.gradient_color(self.rpm_where_bulb_lights(i), config.RPM_STOPS)
            self.assertEqual(color, expected,
                             "bulb {} lights at {} rpm".format(i, self.rpm_where_bulb_lights(i)))

    def test_top_bulb_is_full_redline_color(self):
        """The whole point of the bar: at full scale the last bulb is the
        redline color, not the color of the segment below it."""
        self.assertEqual(self.lit_colors()[-1],
                         ui.gradient_color(config.SHIFT_BAR_MAX_RPM, config.RPM_STOPS))

    def test_no_bulb_lights_blue(self):
        """Blue is the idle color; a lit bulb always means revs are up."""
        self.assertNotIn(config.COLOR_ALERT_BLUE, self.lit_colors())


if __name__ == "__main__":
    unittest.main()
