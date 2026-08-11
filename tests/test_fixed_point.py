# Tests for canbus.format_x10 and ticks.diff - the two pure helpers every
# displayed number and every scheduling decision passes through.

import unittest

import stubs

stubs.install()   # CircuitPython stand-ins + repo path; before any dash import

import canbus
import ticks


class TestFormatX10(unittest.TestCase):
    """format_x10 turns the x10 fixed-point ints into what the driver reads."""

    def test_none_is_dashes(self):
        self.assertEqual(canbus.format_x10(None, 0), "---")
        self.assertEqual(canbus.format_x10(None, 1), "---")

    def test_one_decimal_examples(self):
        self.assertEqual(canbus.format_x10(1755, 1), "175.5")
        self.assertEqual(canbus.format_x10(-1755, 1), "-175.5")
        self.assertEqual(canbus.format_x10(0, 1), "0.0")
        self.assertEqual(canbus.format_x10(-4, 1), "-0.4")

    def test_whole_units_round_half_away_from_zero(self):
        self.assertEqual(canbus.format_x10(14, 0), "1")
        self.assertEqual(canbus.format_x10(15, 0), "2")     # .5 rounds up
        self.assertEqual(canbus.format_x10(-14, 0), "-1")
        self.assertEqual(canbus.format_x10(-15, 0), "-2")   # symmetric

    def test_negative_zero_never_appears(self):
        """-0 on a gauge looks like a fault. -0.4 must round to "0", not "-0"."""
        for v in range(-2000, 2001):
            for decimals in (0, 1):
                self.assertNotEqual(canbus.format_x10(v, decimals), "-0")
                self.assertNotEqual(canbus.format_x10(v, decimals), "-0.0")

    def test_sign_symmetry_over_full_domain(self):
        """Formatting -v must equal formatting v with a minus, whenever the
        rounded magnitude isn't zero. Exhaustive over the realistic domain."""
        for v in range(1, 2001):
            for decimals in (0, 1):
                pos = canbus.format_x10(v, decimals)
                neg = canbus.format_x10(-v, decimals)
                if pos == "0" or pos == "0.0":
                    self.assertEqual(neg, pos)
                else:
                    self.assertEqual(neg, "-" + pos)

    def test_one_decimal_is_exact_not_a_float_round_trip(self):
        """The .1 digit must be the low digit of the int, exactly - this is
        the whole point of keeping values fixed-point."""
        for v in range(0, 3000):
            self.assertEqual(canbus.format_x10(v, 1),
                             "{}.{}".format(v // 10, v % 10))

    def test_int16_extremes(self):
        # CLT/MAT/TPS/battery arrive as signed 16-bit; nothing may crash.
        self.assertEqual(canbus.format_x10(32767, 1), "3276.7")
        self.assertEqual(canbus.format_x10(-32768, 1), "-3276.8")
        self.assertEqual(canbus.format_x10(65535, 1), "6553.5")   # MAP is u16


class TestTicksDiff(unittest.TestCase):
    """Every timeout in the dash is ticks.diff(now, then) >= interval."""

    PERIOD = 1 << 29
    MAX = PERIOD - 1

    def test_simple_forward_and_back(self):
        self.assertEqual(ticks.diff(1000, 900), 100)
        self.assertEqual(ticks.diff(900, 1000), -100)
        self.assertEqual(ticks.diff(42, 42), 0)

    def test_correct_across_the_wrap(self):
        """The case the whole module exists for: `now` has wrapped past zero
        and `then` hasn't. A naive now-then would give a huge negative here,
        freezing every timeout for days."""
        self.assertEqual(ticks.diff(5, self.MAX - 4), 10)
        self.assertEqual(ticks.diff(0, self.MAX), 1)
        self.assertEqual(ticks.diff(self.MAX, 0), -1)

    def test_antisymmetry_for_realistic_gaps(self):
        for gap in (1, 50, 1000, 60000, 1 << 20, self.PERIOD // 4):
            for base in (0, 12345, self.MAX - gap, self.PERIOD // 2):
                a = (base + gap) & self.MAX
                self.assertEqual(ticks.diff(a, base), gap)
                self.assertEqual(ticks.diff(base, a), -gap)

    def test_stale_timeout_holds_across_a_wrap(self):
        """A channel updated just before the wrap must not read as stale the
        instant the counter rolls over."""
        import config
        last = self.MAX - 100
        just_after_wrap = 50            # 151 ms later in real time
        self.assertEqual(ticks.diff(just_after_wrap, last), 151)
        self.assertLess(ticks.diff(just_after_wrap, last), config.STALE_TIMEOUT_MS)


if __name__ == "__main__":
    unittest.main()
