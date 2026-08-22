# Tests for cross-page alarming (F-05), the non-color alarm signal (F-06),
# unit labelling (F-14), and the touch state machine's press handling
# (F-16 / the hold-to-reset gesture).

import unittest

import stubs

stubs.install()   # CircuitPython stand-ins + repo path; before any dash import

import canbus
import config
import touch
import ui


class AlarmTestCase(unittest.TestCase):
    """alarm_param() scans every channel, not just the visible page."""

    NOW = 5000

    def setUp(self):
        self.ecu = canbus.EcuData()
        self.run_engine()

    def run_engine(self, rpm=2000):
        """Alarms are gated on the engine running, so most tests need this."""
        self.ecu.set(config.PARAM_RPM, rpm * 10, self.NOW)

    def set_real(self, param, value):
        self.ecu.set(param, int(value * 10), self.NOW)


class TestAlarmScan(AlarmTestCase):

    def test_nothing_alarms_when_all_is_well(self):
        self.set_real(config.PARAM_CLT, 190.0)
        self.set_real(config.PARAM_BATT, 13.8)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), -1)

    def test_overheat_is_found_from_any_page(self):
        """The finding in one line: this used to be invisible unless you
        happened to be on the coolant page."""
        self.set_real(config.PARAM_CLT, config.CLT_RED_F + 10)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), config.PARAM_CLT)

    def test_limits_are_inclusive_at_the_threshold(self):
        self.set_real(config.PARAM_CLT, config.CLT_RED_F)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), config.PARAM_CLT)

    def test_just_below_the_limit_is_quiet(self):
        self.set_real(config.PARAM_CLT, config.CLT_RED_F - 0.1)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), -1)

    def test_battery_alarms_on_both_sides(self):
        self.set_real(config.PARAM_BATT, config.BATT_LOW_V - 0.5)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), config.PARAM_BATT)
        self.set_real(config.PARAM_BATT, config.BATT_HIGH_V + 0.5)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), config.PARAM_BATT)

    def test_normal_charging_voltage_is_quiet(self):
        for volts in (12.4, 13.5, 14.2, 14.6):
            self.set_real(config.PARAM_BATT, volts)
            self.assertEqual(ui.alarm_param(self.ecu, self.NOW), -1, volts)

    def test_overboost_and_heatsoak_alarm(self):
        self.set_real(config.PARAM_BOOST, config.BOOST_MAX_PSI + 1)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), config.PARAM_BOOST)
        self.setUp()
        self.set_real(config.PARAM_MAT, config.MAT_RED_MIN_F + 5)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), config.PARAM_MAT)

    def test_coolant_outranks_a_simultaneous_boost_alarm(self):
        self.set_real(config.PARAM_CLT, config.CLT_RED_F + 5)
        self.set_real(config.PARAM_BOOST, config.BOOST_MAX_PSI + 5)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), config.PARAM_CLT)

    def test_rpm_never_alarms(self):
        """Deliberate: anyone revving to redline on purpose would get a banner
        every gear change, and an alarm that cries wolf gets ignored."""
        self.run_engine(rpm=config.RPM_RED_AT + 1000)
        self.assertEqual(ui.alarm_param(self.ecu, self.NOW), -1)

    def test_stale_channels_do_not_alarm(self):
        """A channel that stopped arriving already says "---" and raises
        NO CAN; alarming on its last-known value would be noise."""
        self.set_real(config.PARAM_CLT, config.CLT_RED_F + 20)
        much_later = self.NOW + config.STALE_TIMEOUT_MS + 1
        self.ecu.set(config.PARAM_RPM, 20000, much_later)     # keep engine live
        self.assertEqual(ui.alarm_param(self.ecu, much_later), -1)

    def test_quiet_when_the_engine_is_not_running(self):
        """Cold widebands, cranking voltage dips and engine-off battery
        readings are all 'alarming' values that mean nothing out of context."""
        ecu = canbus.EcuData()
        ecu.set(config.PARAM_RPM, 0, self.NOW)
        ecu.set(config.PARAM_BATT, int(config.BATT_LOW_V * 10) - 5, self.NOW)
        self.assertEqual(ui.alarm_param(ecu, self.NOW), -1)

    def test_quiet_when_rpm_itself_is_stale(self):
        ecu = canbus.EcuData()
        ecu.set(config.PARAM_CLT, int((config.CLT_RED_F + 20) * 10), self.NOW)
        self.assertEqual(ui.alarm_param(ecu, self.NOW), -1)

    def test_every_alarm_limit_sits_inside_its_plausibility_range(self):
        """An alarm limit outside SANE_RANGES could never fire - the gate
        would drop the sample first."""
        for param, (lo, hi) in enumerate(config.ALARMS):
            sane_lo, sane_hi = config.SANE_RANGES[param]
            if lo is not None:
                self.assertGreater(lo, sane_lo, config.PAGES[param][0])
            if hi is not None:
                self.assertLess(hi, sane_hi, config.PAGES[param][0])

    def test_priority_list_covers_every_channel_that_has_a_limit(self):
        for param, (lo, hi) in enumerate(config.ALARMS):
            if lo is not None or hi is not None:
                self.assertIn(param, config.ALARM_PRIORITY, config.PAGES[param][0])


class TestNonColorSignal(AlarmTestCase):
    """F-06: color alone is no use to red/green color deficiency or in sun."""

    def test_value_gains_a_bang_in_alarm(self):
        self.assertEqual(ui._value_text(config.PARAM_CLT, 2380, True), "238!")
        self.assertEqual(ui._value_text(config.PARAM_CLT, 2380, False), "238")

    def test_bang_respects_each_channels_decimals(self):
        self.assertEqual(ui._value_text(config.PARAM_BOOST, 214, True), "21.4!")
        self.assertEqual(ui._value_text(config.PARAM_BATT, 152, True), "15.2!")

    def test_alarm_is_legible_without_any_color(self):
        """Strip the color and the alarm must still be readable: the banner
        names the channel and the value carries a "!"."""
        self.set_real(config.PARAM_CLT, 238.0)
        alarm = ui.alarm_param(self.ecu, self.NOW)
        banner = config.PAGES[alarm][0] + " " + canbus.format_x10(
            self.ecu.value_x10[alarm], config.PAGES[alarm][2])
        self.assertEqual(banner, "COOLANT 238")
        self.assertTrue(ui._value_text(alarm, self.ecu.value_x10[alarm], True).endswith("!"))


class TestUnitLabels(unittest.TestCase):
    """F-14: the Overview and Beam pages showed bare numbers - 'BOOST 12.3'
    with no way to know if that is PSI or kPa."""

    def test_units_are_appended(self):
        self.assertEqual(ui._name_with_units(config.PARAM_BOOST), "BOOST PSI")
        self.assertEqual(ui._name_with_units(config.PARAM_CLT), "COOLANT F")
        self.assertEqual(ui._name_with_units(config.PARAM_BATT), "BATTERY V")

    def test_no_redundant_repetition(self):
        self.assertEqual(ui._name_with_units(config.PARAM_RPM), "RPM")
        self.assertEqual(ui._name_with_units(config.PARAM_AFR), "AFR 1")

    def test_overview_labels_fit_their_cells(self):
        """Two columns on a 240 px screen; a clipped label is worse than none."""
        for i, param in enumerate(config.GRID_PARAMS):
            width = len(ui._name_with_units(param)) * 6 * config.GRID_NAME_SCALE
            center_x = config.GRID_NAME_POS[i][0]
            self.assertGreaterEqual(center_x - width // 2, 0)
            self.assertLessEqual(center_x + width // 2, config.SCREEN_W)

    def test_beam_units_clear_the_value_readout(self):
        """The beam value is scale 4 and right-aligned; the name+unit must not
        reach into the widest value it can show."""
        left_x = (config.SCREEN_W - config.BEAM_BAR_W) // 2
        for param, name in ((config.PARAM_BOOST, "BOOST"), (config.PARAM_MAT, "MAT")):
            units = config.PAGES[param][1]
            units_x = left_x + len(name) * 6 * config.BEAM_LABEL_SCALE + 6
            units_end = units_x + len(units) * 6
            lo, hi = config.SANE_RANGES[param]
            widest = max(len(canbus.format_x10(int(v * 10), config.PAGES[param][2]))
                         for v in (lo, hi))
            value_start = (left_x + config.BEAM_BAR_W) - widest * 6 * config.BEAM_VALUE_SCALE
            self.assertLess(units_end, value_start,
                            "{} unit label overlaps its value".format(name))


class FakeTouch:
    """Stands in for the TSC2007 driver: `touched` is a boolean poll,
    `touch` is the dict read that allocates."""

    def __init__(self):
        self.pressed = False
        self.x = 3760
        self.pressure = 500
        self.point_reads = 0

    @property
    def touched(self):
        return self.pressed

    @property
    def touch(self):
        self.point_reads += 1
        return {"x": self.x, "y": 2000, "pressure": self.pressure}


class TouchTestCase(unittest.TestCase):

    def make_nav(self):
        nav = touch.TouchNav.__new__(touch.TouchNav)
        nav._touch = FakeTouch()
        nav._state = touch._IDLE
        nav._last_poll = 0
        nav._light_polls = 0
        nav._press_start = 0
        nav._hold_fired = False
        nav._left_zone_max_x = int(config.SCREEN_W * config.TAP_ZONE_FRACTION)
        return nav

    def poll(self, nav, at):
        return nav.update(at)


class TestTapHandling(TouchTestCase):

    def test_tap_fires_once_per_press(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        t = config.TOUCH_POLL_MS
        self.assertNotEqual(self.poll(nav, t), 0)
        for i in range(1, 5):           # still held
            self.assertEqual(self.poll(nav, t + i * config.TOUCH_POLL_MS), 0)

    def test_release_rearms(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        t = config.TOUCH_POLL_MS
        self.assertNotEqual(self.poll(nav, t), 0)
        nav._touch.pressed = False
        self.poll(nav, t * 2)
        nav._touch.pressed = True
        self.assertNotEqual(self.poll(nav, t * 3), 0)

    def test_left_and_right_zones(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        nav._touch.x = config.TS_RAW_X_MIN          # physical left edge
        self.assertEqual(self.poll(nav, config.TOUCH_POLL_MS), -1)
        nav._touch.pressed = False
        self.poll(nav, config.TOUCH_POLL_MS * 2)
        nav._touch.pressed = True
        nav._touch.x = config.TS_RAW_X_MAX          # physical right edge
        self.assertEqual(self.poll(nav, config.TOUCH_POLL_MS * 3), 1)

    def test_polls_are_rate_limited(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        self.assertEqual(self.poll(nav, config.TOUCH_POLL_MS - 1), 0)
        self.assertEqual(nav._touch.point_reads, 0)


class TestLightPressHandling(TouchTestCase):
    """F-16: a sub-threshold contact used to re-read the driver's point dict
    at the full 50 Hz for as long as anything rested on the panel."""

    def test_a_landing_finger_still_registers(self):
        """Pressure ramps as a finger seats, so the first poll of a good tap
        can read light - rejecting immediately would drop real taps."""
        nav = self.make_nav()
        nav._touch.pressed = True
        nav._touch.pressure = config.TOUCH_PRESSURE_THRESHOLD
        t = config.TOUCH_POLL_MS
        self.assertEqual(self.poll(nav, t), 0)          # too light, retry
        nav._touch.pressure = 800                        # finger seats
        self.assertNotEqual(self.poll(nav, t * 2), 0)

    def test_a_resting_object_is_written_off(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        nav._touch.pressure = 10
        t = config.TOUCH_POLL_MS
        for i in range(1, touch._REJECT_POLLS + 1):
            self.assertEqual(self.poll(nav, t * i), 0)
        reads_at_rejection = nav._touch.point_reads
        for i in range(touch._REJECT_POLLS + 1, touch._REJECT_POLLS + 40):
            self.assertEqual(self.poll(nav, t * i), 0)
        self.assertEqual(nav._touch.point_reads, reads_at_rejection,
                         "point dict still being read after rejection")

    def test_rejection_clears_when_the_object_lifts(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        nav._touch.pressure = 10
        t = config.TOUCH_POLL_MS
        for i in range(1, touch._REJECT_POLLS + 2):
            self.poll(nav, t * i)
        self.assertEqual(nav._state, touch._REJECTED)
        nav._touch.pressed = False
        self.poll(nav, t * 50)
        self.assertEqual(nav._state, touch._IDLE)
        nav._touch.pressed = True
        nav._touch.pressure = 800
        self.assertNotEqual(self.poll(nav, t * 51), 0)


class TestHoldGesture(TouchTestCase):
    """Hold to reset PEAK/LO/AVG - previously only a power cycle could, which
    also ended the datalog session."""

    def test_hold_reports_once(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        t = config.TOUCH_POLL_MS
        self.assertNotEqual(self.poll(nav, t), 0)        # the tap
        held = t + config.TOUCH_HOLD_MS
        self.assertEqual(self.poll(nav, held), touch.HOLD)
        for i in range(1, 20):                           # still held, quiet
            self.assertEqual(self.poll(nav, held + i * t), 0)

    def test_a_normal_tap_never_holds(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        t = config.TOUCH_POLL_MS
        self.poll(nav, t)
        nav._touch.pressed = False
        self.poll(nav, t + 200)                          # lifted well before
        nav._touch.pressed = True
        self.assertNotEqual(self.poll(nav, t + 400), touch.HOLD)

    def test_hold_rearms_for_the_next_press(self):
        nav = self.make_nav()
        nav._touch.pressed = True
        t = config.TOUCH_POLL_MS
        self.poll(nav, t)
        self.assertEqual(self.poll(nav, t + config.TOUCH_HOLD_MS), touch.HOLD)
        nav._touch.pressed = False
        self.poll(nav, t + config.TOUCH_HOLD_MS + t)
        nav._touch.pressed = True
        self.poll(nav, t + config.TOUCH_HOLD_MS + 2 * t)
        self.assertEqual(
            self.poll(nav, t + 2 * config.TOUCH_HOLD_MS + 2 * t), touch.HOLD)

    def test_hold_is_distinct_from_the_page_steps(self):
        self.assertNotIn(touch.HOLD, (-1, 0, 1))


class TestTouchAvailability(unittest.TestCase):
    """F-18: a missing touch chip only ever announced itself on the serial
    console, which needs a laptop to read."""

    def test_available_reflects_the_probe(self):
        nav = touch.TouchNav.__new__(touch.TouchNav)
        nav._touch = None
        self.assertFalse(nav.available)
        nav._touch = FakeTouch()
        self.assertTrue(nav.available)

    def test_update_is_a_noop_without_a_controller(self):
        nav = touch.TouchNav.__new__(touch.TouchNav)
        nav._touch = None
        self.assertEqual(nav.update(1000), 0)


if __name__ == "__main__":
    unittest.main()
