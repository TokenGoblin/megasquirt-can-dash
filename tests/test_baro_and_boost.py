# Tests for the barometric reference capture and the Boost derivation.
#
# This is the file that matters most in the suite. Boost is the only value the
# dash CALCULATES rather than reports, its zero point is measured rather than
# configured, and once the engine starts that zero is frozen for the whole
# drive. A wrong reference here doesn't fail loudly - it produces a plausible
# number that is quietly several PSI out, on the gauge and in the log.

import struct
import unittest

import stubs

stubs.install()   # CircuitPython stand-ins + repo path; before any dash import

import canbus
import config


def make_dash0(map_kpa, rpm, clt_f=180.0, tps_pct=0.0):
    """Build a real base+0 payload: MAP kPa*10 | RPM | CLT degF*10 | TPS %*10."""
    return struct.pack(">HHhh", int(map_kpa * 10), rpm, int(clt_f * 10), int(tps_pct * 10))


class BaroTestCase(unittest.TestCase):
    """Drives the real CanBus._decode with real packed frames. The CAN
    peripheral is never constructed - __new__ plus _init_baro_state() gives a
    decoder with nothing but its barometric state."""

    def setUp(self):
        self.can = canbus.CanBus.__new__(canbus.CanBus)
        self.can._init_baro_state()
        self.ecu = canbus.EcuData()
        self.now = 1000

    def feed(self, map_kpa, rpm, count=1):
        for _ in range(count):
            self.can._decode(canbus._ID_DASH0, make_dash0(map_kpa, rpm), self.ecu, self.now)

    def boost_psi(self):
        return self.ecu.value_x10[config.PARAM_BOOST] / 10.0


class TestBaroLock(BaroTestCase):

    def test_unlocked_until_enough_frames_agree(self):
        self.feed(84.1, rpm=0, count=config.BARO_LOCK_SAMPLES - 1)
        self.assertFalse(self.can.baro_locked)
        self.assertEqual(self.can.baro_ref_x10,
                         int(config.BARO_FALLBACK_KPA * 10 + 0.5))
        self.feed(84.1, rpm=0)          # the sample that completes the run
        self.assertTrue(self.can.baro_locked)
        self.assertEqual(self.can.baro_ref_x10, 841)

    def test_single_garbage_frame_cannot_set_the_reference(self):
        """F-03 regression. A booting ECU emits one implausible-but-in-window
        MAP reading and the driver cranks immediately, freezing the reference.
        Before the consensus requirement this locked 60.0 kPa and put a
        permanent +6 PSI on every boost reading for the rest of the drive."""
        self.feed(60.0, rpm=0)          # one garbage frame at 5,000 ft
        self.feed(900, rpm=900)         # engine catches; reference freezes

        self.assertFalse(self.can.baro_locked)
        self.assertNotEqual(self.can.baro_ref_x10, 600)

        # Running on the honest sea-level fallback, and saying so, beats a
        # frozen wrong measurement that looks real.
        self.feed(101.3, rpm=800)
        self.assertAlmostEqual(self.boost_psi(), 0.0, delta=0.05)

    def test_run_must_be_consecutive(self):
        """Frames that don't qualify break the run - otherwise a garbage
        reading every few seconds would accumulate into a lock."""
        for _ in range(20):
            self.feed(60.0, rpm=0)      # one bad engine-off frame...
            self.feed(101.3, rpm=1200)  # ...then the engine is running again
        self.assertFalse(self.can.baro_locked)

    def test_disagreeing_frames_restart_the_run(self):
        half = config.BARO_LOCK_SAMPLES // 2
        self.feed(84.1, rpm=0, count=half)
        self.feed(70.0, rpm=0)                       # disagrees: run restarts
        self.feed(70.0, rpm=0, count=config.BARO_LOCK_SAMPLES - 2)
        self.assertFalse(self.can.baro_locked)       # one short
        self.feed(70.0, rpm=0)
        self.assertTrue(self.can.baro_locked)
        self.assertEqual(self.can.baro_ref_x10, 700)

    def test_lock_uses_the_mean_of_the_agreeing_run(self):
        """Sensor noise inside the spread should average out, not be taken
        from whichever frame happened to land last."""
        for kpa in (84.0, 84.2, 84.0, 84.2, 84.0, 84.2, 84.0, 84.2):
            self.feed(kpa, rpm=0)
        if config.BARO_LOCK_SAMPLES == 8:
            self.assertEqual(self.can.baro_ref_x10, 841)

    def test_idle_vacuum_is_never_mistaken_for_atmosphere(self):
        """The RPM gate is what makes this safe: at idle MAP is ~35 kPa, well
        inside the plausibility window."""
        self.feed(35.0, rpm=850, count=200)
        self.assertFalse(self.can.baro_locked)

    def test_implausible_values_are_rejected_however_many_arrive(self):
        for kpa in (0.0, 20.0, 54.9, 110.1, 250.0):
            self.setUp()
            self.feed(kpa, rpm=0, count=config.BARO_LOCK_SAMPLES * 3)
            self.assertFalse(self.can.baro_locked,
                             "{} kPa should be outside the window".format(kpa))


class TestBaroTracking(BaroTestCase):

    def lock_at(self, kpa):
        self.feed(kpa, rpm=0, count=config.BARO_LOCK_SAMPLES)
        self.assertTrue(self.can.baro_locked)

    def test_tracks_down_to_a_new_altitude_while_parked(self):
        self.lock_at(101.3)
        self.feed(84.1, rpm=0, count=200)       # drove up a mountain, parked
        self.assertEqual(self.can.baro_ref_x10, 841)

    def test_tracks_up_to_a_new_altitude_while_parked(self):
        """The low-pass shifts right, which floors toward negative infinity;
        small positive deltas need the explicit nudge or the reference stalls
        one step short forever."""
        self.lock_at(84.1)
        self.feed(101.3, rpm=0, count=200)
        self.assertEqual(self.can.baro_ref_x10, 1013)

    def test_frozen_while_the_engine_runs(self):
        self.lock_at(101.3)
        self.feed(250.0, rpm=4000, count=100)   # 2.5 bar of boost
        self.assertEqual(self.can.baro_ref_x10, 1013)

    def test_override_pins_the_reference_against_everything(self):
        saved = canbus._BARO_OVERRIDE_X10
        try:
            canbus._BARO_OVERRIDE_X10 = 1013
            self.can._init_baro_state()
            self.assertTrue(self.can.baro_locked)
            self.feed(84.1, rpm=0, count=500)
            self.assertEqual(self.can.baro_ref_x10, 1013)
        finally:
            canbus._BARO_OVERRIDE_X10 = saved


class TestBoostDerivation(BaroTestCase):

    def test_reads_exactly_zero_with_the_engine_off(self):
        """The headline promise: 0.0 PSI engine-off at any altitude."""
        for kpa in (101.3, 84.1, 95.0, 60.0):
            self.setUp()
            self.feed(kpa, rpm=0, count=config.BARO_LOCK_SAMPLES)
            if self.can.baro_locked:
                self.assertEqual(self.boost_psi(), 0.0)

    def test_conversion_accurate_across_the_whole_map_range(self):
        self.feed(101.3, rpm=0, count=config.BARO_LOCK_SAMPLES)
        ref = self.can.baro_ref_x10 / 10.0
        for map_tenths in range(200, 3001):          # 20.0 .. 300.0 kPa
            kpa = map_tenths / 10.0
            self.feed(kpa, rpm=3000)
            expected = (kpa - ref) / config.KPA_PER_PSI
            self.assertAlmostEqual(
                self.boost_psi(), expected, delta=0.05,
                msg="MAP {} kPa derived {} PSI, expected {:.3f}".format(
                    kpa, self.boost_psi(), expected))

    def test_vacuum_reads_negative(self):
        self.feed(101.3, rpm=0, count=config.BARO_LOCK_SAMPLES)
        self.feed(35.0, rpm=800)                     # idle
        self.assertLess(self.boost_psi(), -9.0)

    def test_rounds_away_from_zero_on_both_sides(self):
        self.feed(101.3, rpm=0, count=config.BARO_LOCK_SAMPLES)
        self.feed(200.0, rpm=4000)
        self.assertEqual(self.ecu.value_x10[config.PARAM_BOOST], 143)   # 14.3
        self.feed(90.0, rpm=800)
        self.assertEqual(self.ecu.value_x10[config.PARAM_BOOST], -16)   # -1.6

    def test_boost_goes_stale_exactly_when_map_does(self):
        """Boost is stamped with the same tick as the MAP frame it came from,
        so a dead bus can't leave a derived value looking live."""
        self.feed(101.3, rpm=0, count=config.BARO_LOCK_SAMPLES)
        late = self.now + config.STALE_TIMEOUT_MS + 1
        self.assertEqual(self.ecu.is_stale(config.PARAM_BOOST, late),
                         self.ecu.is_stale(config.PARAM_MAP, late))
        self.assertTrue(self.ecu.is_stale(config.PARAM_BOOST, late))


if __name__ == "__main__":
    unittest.main()
