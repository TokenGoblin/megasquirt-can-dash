# Tests for config.validate() - the startup check that turns a mis-edited
# setting into a named message on the fatal screen instead of a
# ZeroDivisionError three pages into the UI an hour later.

import contextlib
import io
import unittest

import stubs

stubs.install()   # CircuitPython stand-ins + repo path; before any dash import

import config


def validate_quietly():
    """validate() prints every problem it finds to the serial console, which
    is right on the dash and noise in a test run. Swallow it here; the return
    value is what these tests are about."""
    with contextlib.redirect_stdout(io.StringIO()):
        return config.validate()


class ConfigTestCase(unittest.TestCase):
    """Each test mutates one setting, checks validate() catches it, and puts
    the setting back - config is a module, so the restore matters."""

    def assert_rejected(self, setting, bad_value, expect_name=None):
        original = getattr(config, setting)
        try:
            setattr(config, setting, bad_value)
            result = validate_quietly()
            self.assertIsNotNone(
                result, "{} = {!r} should have been rejected".format(setting, bad_value))
            self.assertLessEqual(len(result), 20,
                                 "message must fit the fatal screen: " + result)
            if expect_name:
                self.assertIn(expect_name, result)
        finally:
            setattr(config, setting, original)


class TestShippedConfigIsValid(ConfigTestCase):

    def test_the_config_as_shipped_passes(self):
        self.assertIsNone(validate_quietly())


class TestDivisorsThatWouldCrashLater(ConfigTestCase):

    def test_equal_touch_calibration_endpoints(self):
        """The README tells users to SWAP these two to fix reversed
        navigation; equalizing them divided by zero on the first tap."""
        self.assert_rejected("TS_RAW_X_MAX", config.TS_RAW_X_MIN, "TS_RAW_X")

    def test_zero_width_bar_range(self):
        """Divided by zero the first time you navigated to that page - a dash
        that worked fine until you pressed 'next' four times."""
        self.assert_rejected("CLT_BAR_MAX_F", config.CLT_BAR_MIN_F, "CLT_BAR")

    def test_inverted_bar_range(self):
        self.assert_rejected("MAT_BAR_MAX_F", config.MAT_BAR_MIN_F - 10.0, "MAT_BAR")

    def test_zero_shift_bar_scale(self):
        self.assert_rejected("SHIFT_BAR_MAX_RPM", 0, "SHIFT_BAR_RPM")

    def test_zero_shift_bar_segments(self):
        self.assert_rejected("SHIFT_BAR_SEGMENTS", 0, "SHIFT_SEGMENTS")

    def test_afr_center_outside_its_own_bar(self):
        self.assert_rejected("AFR_BAR_CENTER", 25.0, "AFR_BAR")


class TestCanSettings(ConfigTestCase):

    def test_base_id_too_high_for_four_standard_ids(self):
        """base+3 has to remain a valid 11-bit ID."""
        self.assert_rejected("BASE_CAN_ID", 2045, "BASE_CAN_ID")

    def test_negative_base_id(self):
        self.assert_rejected("BASE_CAN_ID", -1, "BASE_CAN_ID")

    def test_zero_baud_rate(self):
        self.assert_rejected("CAN_BAUD_RATE", 0, "CAN_BAUD_RATE")

    def test_zero_frames_per_update_would_decode_nothing(self):
        self.assert_rejected("CAN_MAX_FRAMES_PER_UPDATE", 0, "CAN_MAX_FRAMES")

    def test_byte_offset_past_the_end_of_a_frame(self):
        self.assert_rejected("OFS_MAT", 7, "OFS_MAT")     # needs 2 bytes at 7
        self.assert_rejected("OFS_AFR1", 8, "OFS_AFR1")


class TestBaroSettings(ConfigTestCase):

    def test_inverted_plausibility_window(self):
        self.assert_rejected("BARO_MAX_KPA", config.BARO_MIN_KPA - 1.0, "BARO_RANGE")

    def test_zero_lock_samples(self):
        self.assert_rejected("BARO_LOCK_SAMPLES", 0, "BARO_SAMPLES")

    def test_negative_override(self):
        self.assert_rejected("ATMOSPHERIC_KPA_OVERRIDE", -5.0, "BARO_OVERRIDE")

    def test_none_override_is_allowed(self):
        original = config.ATMOSPHERIC_KPA_OVERRIDE
        try:
            config.ATMOSPHERIC_KPA_OVERRIDE = None
            self.assertIsNone(validate_quietly())
            config.ATMOSPHERIC_KPA_OVERRIDE = 101.3
            self.assertIsNone(validate_quietly())
        finally:
            config.ATMOSPHERIC_KPA_OVERRIDE = original


class TestTimingSettings(ConfigTestCase):

    def test_zero_intervals_are_rejected(self):
        for setting, name in (("STALE_TIMEOUT_MS", "STALE_TIMEOUT"),
                              ("UI_TICK_MS", "UI_TICK_MS"),
                              ("TOUCH_POLL_MS", "TOUCH_POLL_MS"),
                              ("LOG_INTERVAL_MS", "LOG_INTERVAL_MS"),
                              ("FAULT_CLEAR_MS", "FAULT_CLEAR_MS")):
            self.assert_rejected(setting, 0, name)

    def test_negative_watchdog(self):
        self.assert_rejected("WATCHDOG_TIMEOUT_S", -1.0, "WATCHDOG")

    def test_watchdog_zero_means_off_not_invalid(self):
        original = config.WATCHDOG_TIMEOUT_S
        try:
            config.WATCHDOG_TIMEOUT_S = 0.0
            self.assertIsNone(validate_quietly())
        finally:
            config.WATCHDOG_TIMEOUT_S = original

    def test_fault_threshold_below_one(self):
        self.assert_rejected("MAX_FAULTS_BEFORE_HALT", 0, "MAX_FAULTS")


class TestLayoutAndPages(ConfigTestCase):

    def test_tap_zone_fraction_outside_zero_to_one(self):
        """1.0 makes the 'next page' zone unreachable - no crash, just half a
        dash you can't navigate."""
        for bad in (0.0, 1.0, -0.5, 2.0):
            self.assert_rejected("TAP_ZONE_FRACTION", bad, "TAP_ZONE")

    def test_unsupported_display_rotation(self):
        self.assert_rejected("DISPLAY_ROTATION", 45, "ROTATION")

    def test_supported_rotations_pass(self):
        original = config.DISPLAY_ROTATION
        try:
            for good in (0, 90, 180, 270):
                config.DISPLAY_ROTATION = good
                self.assertIsNone(validate_quietly())
        finally:
            config.DISPLAY_ROTATION = original

    def test_pages_decimals_must_be_zero_or_one(self):
        original = config.PAGES
        try:
            config.PAGES = original[:-1] + (("MAT", "F", 3),)
            result = validate_quietly()
            self.assertIsNotNone(result)
            self.assertIn("PAGES", result)
        finally:
            config.PAGES = original

    def test_grid_position_lists_must_line_up(self):
        original = config.GRID_NAME_POS
        try:
            config.GRID_NAME_POS = original[:-1]
            result = validate_quietly()
            self.assertIsNotNone(result)
            self.assertIn("GRID", result)
        finally:
            config.GRID_NAME_POS = original

    def test_grid_param_out_of_range(self):
        original = config.GRID_PARAMS
        try:
            config.GRID_PARAMS = (config.PARAM_BOOST, 99, config.PARAM_AFR,
                                  config.PARAM_BATT)
            result = validate_quietly()
            self.assertIsNotNone(result)
            self.assertIn("GRID", result)
        finally:
            config.GRID_PARAMS = original


class TestGateAndAlarmSettings(ConfigTestCase):

    def test_short_sane_ranges_is_caught_at_startup(self):
        """A missing entry would IndexError inside EcuData.set - i.e. on the
        first CAN frame, from a traceback that names neither the setting nor
        this file."""
        original = config.SANE_RANGES
        try:
            config.SANE_RANGES = original[:-1]
            result = validate_quietly()
            self.assertIsNotNone(result)
            self.assertIn("SANE", result)
        finally:
            config.SANE_RANGES = original

    def test_inverted_sane_range(self):
        original = config.SANE_RANGES
        try:
            config.SANE_RANGES = ((100.0, 0.0),) + original[1:]
            self.assertIsNotNone(validate_quietly())
        finally:
            config.SANE_RANGES = original

    def test_alarm_limit_outside_the_plausibility_gate_is_caught(self):
        """Such a limit can never fire: the sample is dropped before anything
        compares it. Silently impossible alarms are worse than none."""
        original = config.ALARMS
        try:
            config.ALARMS = original[:3] + ((None, 9999.0),) + original[4:]
            result = validate_quietly()
            self.assertIsNotNone(result)
            self.assertIn("ALARM", result)
        finally:
            config.ALARMS = original

    def test_alarm_limits_must_not_cross(self):
        original = config.ALARMS
        try:
            config.ALARMS = original[:6] + ((15.0, 12.0),) + original[7:]
            self.assertIsNotNone(validate_quietly())
        finally:
            config.ALARMS = original

    def test_channel_with_limits_missing_from_the_priority_list(self):
        original = config.ALARM_PRIORITY
        try:
            config.ALARM_PRIORITY = tuple(
                p for p in original if p != config.PARAM_CLT)
            result = validate_quietly()
            self.assertIsNotNone(result)
            self.assertIn("ALARM", result)
        finally:
            config.ALARM_PRIORITY = original

    def test_zero_blink_period(self):
        self.assert_rejected("ALARM_BLINK_MS", 0, "ALARM_BLINK")

    def test_zero_hold_time(self):
        self.assert_rejected("TOUCH_HOLD_MS", 0, "TOUCH_HOLD_MS")


class TestMessageShape(ConfigTestCase):

    def test_every_message_fits_the_fatal_screen(self):
        """show_fatal renders at scale 2: about 20 characters across 240 px.
        A message that overflows is worse than no message."""
        for setting, bad in (("BASE_CAN_ID", 9999), ("UI_TICK_MS", 0),
                             ("TAP_ZONE_FRACTION", 5.0), ("DISPLAY_ROTATION", 1),
                             ("SHIFT_BAR_SEGMENTS", 0), ("OFS_MAP", 99)):
            original = getattr(config, setting)
            try:
                setattr(config, setting, bad)
                message = validate_quietly()
                self.assertIsNotNone(message)
                self.assertLessEqual(len(message), 20, message)
                self.assertTrue(message.startswith("CFG: "), message)
            finally:
                setattr(config, setting, original)


if __name__ == "__main__":
    unittest.main()
