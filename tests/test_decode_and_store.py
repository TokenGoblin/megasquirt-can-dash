# Tests for the CAN frame decode and the EcuData live store: the trust
# boundary (everything here arrives from a bus the dash doesn't control) and
# the statistics every readout is built from.

import contextlib
import io
import struct
import unittest

import stubs

stubs.install()   # CircuitPython stand-ins + repo path; before any dash import

import canbus
import config


class TestDecode(unittest.TestCase):

    def setUp(self):
        self.can = canbus.CanBus.__new__(canbus.CanBus)
        self.can._init_baro_state()
        self.ecu = canbus.EcuData()

    def test_dash0_unpacks_all_four_words(self):
        payload = struct.pack(">HHhh", 1013, 3200, 1955, 452)
        self.can._decode(canbus._ID_DASH0, payload, self.ecu, 500)
        self.assertEqual(self.ecu.value_x10[config.PARAM_MAP], 1013)
        self.assertEqual(self.ecu.value_x10[config.PARAM_RPM], 32000)  # x1 -> x10
        self.assertEqual(self.ecu.value_x10[config.PARAM_CLT], 1955)
        self.assertEqual(self.ecu.value_x10[config.PARAM_TPS], 452)

    def test_temperatures_decode_below_zero(self):
        """CLT and MAT are signed for exactly this reason - a cold morning
        must not read as +6500 degrees."""
        payload = struct.pack(">HHhh", 1013, 0, -220, 0)     # -22.0 F
        self.can._decode(canbus._ID_DASH0, payload, self.ecu, 500)
        self.assertEqual(self.ecu.value_x10[config.PARAM_CLT], -220)

        mat = bytearray(8)
        struct.pack_into(">h", mat, config.OFS_MAT, -150)
        self.can._decode(canbus._ID_DASH1, bytes(mat), self.ecu, 500)
        self.assertEqual(self.ecu.value_x10[config.PARAM_MAT], -150)

    def test_afr_is_one_unsigned_byte_not_a_word(self):
        """The byte before AFR1 is the AFR TARGET. Merging them was a real bug
        once; this pins the layout so it can't come back."""
        payload = bytearray(8)
        payload[0] = 147            # afrtgt1 - must be ignored
        payload[config.OFS_AFR1] = 128
        self.can._decode(canbus._ID_DASH2, bytes(payload), self.ecu, 500)
        self.assertEqual(self.ecu.value_x10[config.PARAM_AFR], 128)   # 12.8 AFR

    def test_battery_decodes_from_dash3(self):
        payload = bytearray(8)
        struct.pack_into(">h", payload, config.OFS_BATT, 138)
        self.can._decode(canbus._ID_DASH3, bytes(payload), self.ecu, 500)
        self.assertEqual(self.ecu.value_x10[config.PARAM_BATT], 138)  # 13.8 V

    def test_unknown_ids_are_ignored(self):
        before = list(self.ecu.value_x10)
        self.can._decode(config.BASE_CAN_ID + 9, bytes(8), self.ecu, 500)
        self.assertEqual(list(self.ecu.value_x10), before)

    def test_all_frames_stamp_their_channels_fresh(self):
        payload = struct.pack(">HHhh", 1013, 800, 1800, 0)
        self.can._decode(canbus._ID_DASH0, payload, self.ecu, 12345)
        for param in (config.PARAM_MAP, config.PARAM_RPM, config.PARAM_CLT,
                      config.PARAM_TPS, config.PARAM_BOOST):
            self.assertEqual(self.ecu.updated[param], 12345)


class _FakeController:
    """Minimal stand-in for what canio.CAN() hands back."""

    def __init__(self):
        self.listen_kwargs = None
        self.state = 0

    def listen(self, **kwargs):
        self.listen_kwargs = kwargs
        return object()


class _FakeCanio:
    """Fake canio module whose CAN() can be told whether this 'build'
    understands the `silent` keyword."""

    class BusState:
        ERROR_ACTIVE = 0
        ERROR_WARNING = 1
        ERROR_PASSIVE = 2
        BUS_OFF = 3

    def __init__(self, accepts_silent):
        self.accepts_silent = accepts_silent
        self.calls = []
        self.controller = None

    def CAN(self, **kwargs):
        if "silent" in kwargs and not self.accepts_silent:
            raise TypeError("unexpected keyword argument 'silent'")
        self.calls.append(kwargs)
        self.controller = _FakeController()
        return self.controller

    def Match(self, *args, **kwargs):
        return ("match", args, kwargs)


class TestCanBusOpen(unittest.TestCase):
    """The `silent` keyword is documented but canio is port-specific, so the
    open has to cope with a build that doesn't know it - without ever silently
    downgrading a silent-mode request."""

    def setUp(self):
        self._saved_canio = canbus.canio
        self._saved_mode = config.CAN_SILENT_MODE
        self.addCleanup(self.restore)

    def restore(self):
        canbus.canio = self._saved_canio
        config.CAN_SILENT_MODE = self._saved_mode

    def open_with(self, accepts_silent, silent_mode):
        fake = _FakeCanio(accepts_silent)
        canbus.canio = fake
        config.CAN_SILENT_MODE = silent_mode
        bus = canbus.CanBus()
        return fake, bus

    def test_silent_is_passed_when_the_build_supports_it(self):
        fake, _ = self.open_with(accepts_silent=True, silent_mode=True)
        self.assertEqual(fake.calls[-1]["silent"], True)

    def test_normal_mode_passes_silent_false(self):
        fake, _ = self.open_with(accepts_silent=True, silent_mode=False)
        self.assertEqual(fake.calls[-1]["silent"], False)

    def test_falls_back_cleanly_on_a_build_without_the_keyword(self):
        """Not asking for silent mode: a plain open is exactly equivalent, so
        the dash should come up rather than show CAN INIT FAILED."""
        fake, bus = self.open_with(accepts_silent=False, silent_mode=False)
        self.assertNotIn("silent", fake.calls[-1])
        self.assertEqual(fake.calls[-1]["baudrate"], config.CAN_BAUD_RATE)
        self.assertIsNotNone(bus)

    def test_refuses_rather_than_downgrade_a_silent_request(self):
        """The one that matters. Falling back here would hand back a
        TRANSMITTING controller to someone who configured a silent one - the
        setting would read as honoured while the dash talked to the bus
        anyway. Fail closed instead."""
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            with self.assertRaises(TypeError):
                self.open_with(accepts_silent=False, silent_mode=True)
        # And say why on the console, or the fatal screen is a mystery.
        self.assertIn("CAN_SILENT_MODE", printed.getvalue())

    def test_auto_restart_is_always_requested(self):
        for accepts in (True, False):
            fake, _ = self.open_with(accepts_silent=accepts, silent_mode=False)
            self.assertTrue(fake.calls[-1]["auto_restart"])

    def test_one_hardware_filter_per_dash_frame(self):
        """Python must never spend time receiving-and-discarding traffic the
        controller could have rejected in silicon - on a busy car bus that is
        the difference between touching 4 IDs and touching all of them."""
        fake, _ = self.open_with(accepts_silent=True, silent_mode=False)
        matches = fake.controller.listen_kwargs["matches"]
        self.assertEqual(len(matches), 4)
        ids = {m[1][0] for m in matches}       # ("match", args, kwargs)
        self.assertEqual(ids, {config.BASE_CAN_ID + n for n in range(4)})

    def test_listener_is_non_blocking(self):
        """timeout=0 is the contract the whole cooperative loop rests on: a
        blocking receive() would stall rendering and touch behind CAN."""
        fake, _ = self.open_with(accepts_silent=True, silent_mode=False)
        self.assertEqual(fake.controller.listen_kwargs["timeout"], 0)


class TestEcuData(unittest.TestCase):

    def setUp(self):
        self.ecu = canbus.EcuData()

    def test_untouched_channels_start_stale(self):
        for param in range(config.NUM_PARAMS):
            self.assertTrue(self.ecu.is_stale(param, 0))

    def test_stale_boundary_is_strictly_greater_than_the_timeout(self):
        self.ecu.set(config.PARAM_RPM, 8000, 1000)
        at_limit = 1000 + config.STALE_TIMEOUT_MS
        self.assertFalse(self.ecu.is_stale(config.PARAM_RPM, at_limit))
        self.assertTrue(self.ecu.is_stale(config.PARAM_RPM, at_limit + 1))

    def test_peak_and_low_track_extremes(self):
        for v in (100, 250, 80, 200):
            self.ecu.set(config.PARAM_AFR, v, 1000)
        self.assertEqual(self.ecu.peak_x10[config.PARAM_AFR], 250)
        self.assertEqual(self.ecu.low_x10[config.PARAM_AFR], 80)

    def test_peak_is_tracked_for_every_channel(self):
        """Every single-gauge page shows a PEAK, so every channel needs one."""
        for param in range(config.NUM_PARAMS):
            lo, hi = config.SANE_RANGES[param]
            sample = int((lo + hi) / 2 * 10)
            self.ecu.set(param, sample, 1000)
            self.assertEqual(self.ecu.peak_x10[param], sample)

    def test_low_and_average_only_where_they_are_read(self):
        """F-09: LO is an AFR readout and AVG is a Battery readout. Keeping
        them for all eight channels cost an accumulator that outgrew
        CircuitPython's small ints mid-drive and started allocating on the
        decode path."""
        for param in range(config.NUM_PARAMS):
            lo, hi = config.SANE_RANGES[param]
            self.ecu.set(param, int((lo + hi) / 2 * 10), 1000)
        for param in range(config.NUM_PARAMS):
            if param in config.LOW_PARAMS:
                self.assertIsNotNone(self.ecu.low_x10[param])
            else:
                self.assertIsNone(self.ecu.low_x10[param])
            if param in config.AVG_PARAMS:
                self.assertIsNotNone(self.ecu.average_x10(param))
            else:
                self.assertIsNone(self.ecu.average_x10(param))

    def test_tracked_accumulator_stays_a_small_int_over_a_long_drive(self):
        """The Battery accumulator is the only one left; at ~18 Hz it must
        stay inside MicroPython's 31-bit small ints for far longer than any
        drive, or the allocation problem simply moves rather than going away."""
        samples_per_hour = 18 * 3600
        worst_sample = int(config.SANE_RANGES[config.PARAM_BATT][1] * 10)
        self.assertLess(samples_per_hour * 24 * worst_sample, (1 << 30) - 1)

    def test_peak_survives_staleness(self):
        """Documented behavior: peaks are for the whole power-on session and
        deliberately outlive a dropout."""
        self.ecu.set(config.PARAM_MAP, 2500, 1000)
        very_late = 1000 + config.STALE_TIMEOUT_MS * 100
        self.assertTrue(self.ecu.is_stale(config.PARAM_MAP, very_late))
        self.assertEqual(self.ecu.peak_x10[config.PARAM_MAP], 2500)

    def test_average_is_none_before_any_sample(self):
        """Divide-by-zero bait: the Battery page asks for this on the very
        first render, before a single frame has arrived."""
        self.assertIsNone(self.ecu.average_x10(config.PARAM_BATT))

    def test_average_rounds_half_up(self):
        self.ecu.set(config.PARAM_BATT, 120, 0)
        self.ecu.set(config.PARAM_BATT, 121, 0)
        self.assertEqual(self.ecu.average_x10(config.PARAM_BATT), 121)  # 120.5

    def test_single_sample_average_is_that_sample(self):
        self.ecu.set(config.PARAM_BATT, 137, 0)
        self.assertEqual(self.ecu.average_x10(config.PARAM_BATT), 137)

    def test_negative_values_do_not_break_the_statistics(self):
        """Sub-zero coolant and intake temperatures are real; the peak has to
        cope with an all-negative sample set."""
        for v in (-200, -50, -300):
            self.ecu.set(config.PARAM_CLT, v, 0)
        self.assertEqual(self.ecu.peak_x10[config.PARAM_CLT], -50)

    def test_reset_stats_clears_every_readout(self):
        """The hold gesture's job: peaks are a whole-session record, but the
        only way to clear one used to be a power cycle - which also ends the
        datalog session."""
        self.ecu.set(config.PARAM_MAP, 2500, 1000)
        self.ecu.set(config.PARAM_AFR, 120, 1000)
        self.ecu.set(config.PARAM_BATT, 138, 1000)
        self.ecu.reset_stats()
        for param in range(config.NUM_PARAMS):
            self.assertIsNone(self.ecu.peak_x10[param])
            self.assertIsNone(self.ecu.low_x10[param])
            self.assertIsNone(self.ecu.average_x10(param))

    def test_reset_stats_leaves_live_values_alone(self):
        """Resetting the history must not blank the gauges."""
        self.ecu.set(config.PARAM_MAP, 1013, 1000)
        self.ecu.reset_stats()
        self.assertEqual(self.ecu.value_x10[config.PARAM_MAP], 1013)
        self.assertFalse(self.ecu.is_stale(config.PARAM_MAP, 1000))

    def test_peaks_rebuild_after_a_reset(self):
        self.ecu.set(config.PARAM_MAP, 2500, 1000)
        self.ecu.reset_stats()
        self.ecu.set(config.PARAM_MAP, 1013, 1100)
        self.assertEqual(self.ecu.peak_x10[config.PARAM_MAP], 1013)


class TestPlausibilityGate(unittest.TestCase):
    """F-07: CAN carries no authentication, so a corrupt frame that passes CRC
    used to flow straight into the display, into PEAK/LO (which persist for
    the session), and into the CSV log."""

    def setUp(self):
        self.ecu = canbus.EcuData()
        self.can = canbus.CanBus.__new__(canbus.CanBus)
        self.can._init_baro_state()

    def test_in_range_samples_are_accepted(self):
        self.assertTrue(self.ecu.set(config.PARAM_MAP, 1013, 100))
        self.assertTrue(self.ecu.set(config.PARAM_CLT, 1955, 100))

    def test_out_of_range_samples_are_rejected(self):
        for param, bad in ((config.PARAM_MAP, 65535),      # 6553.5 kPa
                           (config.PARAM_RPM, 300000),     # 30,000 rpm
                           (config.PARAM_CLT, -32768),
                           (config.PARAM_BATT, 500)):      # 50 V
            self.assertFalse(self.ecu.set(param, bad, 100),
                             "param {} accepted {}".format(param, bad))

    def test_a_rejected_sample_changes_nothing_at_all(self):
        """Not stored, not peaked, not timestamped - so the channel goes
        stale and shows "---" rather than a believable wrong number."""
        self.ecu.set(config.PARAM_MAP, 1013, 100)
        self.ecu.set(config.PARAM_MAP, 65535, 200)
        self.assertEqual(self.ecu.value_x10[config.PARAM_MAP], 1013)
        self.assertEqual(self.ecu.peak_x10[config.PARAM_MAP], 1013)
        self.assertEqual(self.ecu.updated[config.PARAM_MAP], 100)

    def test_one_corrupt_frame_cannot_poison_peak_for_the_session(self):
        """The concrete symptom: "PEAK 6553.5" stuck on the MAP page for the
        rest of the drive with no way to clear it."""
        payload = struct.pack(">HHhh", 65535, 3000, 1900, 500)
        self.can._decode(canbus._ID_DASH0, payload, self.ecu, 100)
        self.assertIsNone(self.ecu.peak_x10[config.PARAM_MAP])

    def test_cold_wideband_zero_does_not_pin_the_afr_low_marker(self):
        """A cold or unplugged wideband reports 0.0, and LO tracks the lowest
        AFR ever seen - so "LO 0.0" used to sit there all session, making the
        readout that would show a dangerous lean excursion decorative."""
        payload = bytearray(8)
        payload[config.OFS_AFR1] = 0
        self.can._decode(canbus._ID_DASH2, bytes(payload), self.ecu, 100)
        self.assertIsNone(self.ecu.low_x10[config.PARAM_AFR])
        self.assertTrue(self.ecu.is_stale(config.PARAM_AFR, 100))

        payload[config.OFS_AFR1] = 147          # sensor comes alive
        self.can._decode(canbus._ID_DASH2, bytes(payload), self.ecu, 200)
        self.assertEqual(self.ecu.low_x10[config.PARAM_AFR], 147)

    def test_boost_is_not_derived_from_a_rejected_map(self):
        """Deriving from a value we just refused to display would smuggle it
        back in through the Boost page."""
        payload = struct.pack(">HHhh", 65535, 3000, 1900, 500)
        self.can._decode(canbus._ID_DASH0, payload, self.ecu, 100)
        self.assertTrue(self.ecu.is_stale(config.PARAM_BOOST, 100))

    def test_plausible_extremes_still_get_through(self):
        """The bounds reject the impossible, not the merely dramatic - a
        genuine 3 bar of boost or a 240 F overheat must still display."""
        payload = struct.pack(">HHhh", 3000, 7200, 2400, 1000)
        self.can._decode(canbus._ID_DASH0, payload, self.ecu, 100)
        self.assertEqual(self.ecu.value_x10[config.PARAM_MAP], 3000)
        self.assertEqual(self.ecu.value_x10[config.PARAM_CLT], 2400)
        self.assertFalse(self.ecu.is_stale(config.PARAM_BOOST, 100))


if __name__ == "__main__":
    unittest.main()
