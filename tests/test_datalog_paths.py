# Tests for session-file numbering and the touch tap-zone mapping.
#
# The file numbering used to list the whole log directory on every engine
# start, which eventually ran the board out of heap at the worst possible
# moment. These tests pin both halves of the replacement: correctness (never
# hand back a name already in use) and cost (a bounded number of stats).

import contextlib
import io
import unittest

import stubs

stubs.install()   # CircuitPython stand-ins + repo path; before any dash import

import config
import datalog
import touch


class FakeCard:
    """Stands in for os.stat/os.mkdir against an in-memory set of file
    numbers, counting how many stats the code performs."""

    def __init__(self, existing=()):
        self.files = set(existing)
        self.stats = 0
        self.made_dirs = []

    def stat(self, path):
        self.stats += 1
        for n in self.files:
            if path == datalog.DataLogger._log_path(n):
                return (0,) * 10
        raise OSError(2, "No such file")

    def mkdir(self, path):
        if path in self.made_dirs:
            raise OSError(17, "File exists")
        self.made_dirs.append(path)


class LoggerTestCase(unittest.TestCase):
    """Builds a DataLogger without touching __init__ (which would want an SD
    card) and redirects the module's `os` at the fake card."""

    def make_logger(self, existing=()):
        card = FakeCard(existing)
        logger = datalog.DataLogger.__new__(datalog.DataLogger)
        logger._file = None
        logger._unavailable = False
        logger._next_n = 1
        self._saved_os = datalog.os
        datalog.os = card
        self.addCleanup(self.restore_os)
        return logger, card

    def restore_os(self):
        datalog.os = self._saved_os


class TestLogNumbering(LoggerTestCase):

    def test_empty_card_starts_at_one(self):
        logger, _ = self.make_logger()
        self.assertEqual(logger._prepare_log_dir(), 1)

    def test_creates_the_log_directory(self):
        logger, card = self.make_logger()
        logger._prepare_log_dir()
        self.assertEqual(card.made_dirs, [config.LOG_DIR])

    def test_existing_directory_is_not_an_error(self):
        logger, card = self.make_logger()
        card.made_dirs.append(config.LOG_DIR)       # mkdir will raise EEXIST
        self.assertEqual(logger._prepare_log_dir(), 1)

    def test_continues_after_the_highest_file(self):
        for count in (1, 2, 3, 7, 8, 100, 999, 1000):
            logger, _ = self.make_logger(range(1, count + 1))
            self.assertEqual(logger._prepare_log_dir(), count + 1)

    def test_path_format_is_zero_padded(self):
        self.assertEqual(datalog.DataLogger._log_path(1), config.LOG_DIR + "/log001.csv")
        self.assertEqual(datalog.DataLogger._log_path(42), config.LOG_DIR + "/log042.csv")
        self.assertEqual(datalog.DataLogger._log_path(1234), config.LOG_DIR + "/log1234.csv")

    def test_never_returns_a_number_already_in_use(self):
        """The one thing that must never happen: handing back a name that
        would truncate somebody's existing log."""
        import random
        random.seed(1234)
        for _ in range(500):
            existing = set(random.sample(range(1, 300), random.randint(0, 80)))
            logger, _ = self.make_logger(existing)
            self.assertNotIn(logger._prepare_log_dir(), existing)
            self.restore_os()

    def test_boot_probe_cost_is_logarithmic(self):
        """F-04 regression. The old code built a list of every filename on the
        card; this must stay bounded well below that even on a full card."""
        logger, card = self.make_logger(range(1, 1001))
        logger._prepare_log_dir()
        self.assertLess(card.stats, 40)

    def test_per_session_cost_is_one_stat(self):
        logger, card = self.make_logger(range(1, 301))
        logger._next_n = logger._prepare_log_dir()
        card.stats = 0
        n = logger._first_free_index()
        self.assertEqual(n, 301)
        self.assertEqual(card.stats, 1)

    def test_recovers_if_the_remembered_number_is_taken(self):
        logger, _ = self.make_logger({1, 2, 3})
        logger._next_n = 2                      # stale bookkeeping
        self.assertEqual(logger._first_free_index(), 4)

    def test_successive_sessions_advance(self):
        logger, card = self.make_logger()
        logger._next_n = logger._prepare_log_dir()
        seen = []
        for _ in range(5):
            n = logger._first_free_index()
            seen.append(n)
            card.files.add(n)                   # _start() opens the file
            logger._next_n = n + 1              # _start() bookkeeping
        self.assertEqual(seen, [1, 2, 3, 4, 5])


class FakeFile:
    """Stands in for an open log file. `fail_after` write calls, it starts
    raising OSError - a card pulled, full, or gone flaky mid-drive."""

    def __init__(self, fail_after=None, fail_on_close=False):
        self.lines = []
        self.fail_after = fail_after
        self.fail_on_close = fail_on_close
        self.closed = False

    def write(self, text):
        if self.fail_after is not None and len(self.lines) >= self.fail_after:
            raise OSError(5, "Input/output error")
        self.lines.append(text)

    def flush(self):
        if self.fail_after is not None and len(self.lines) >= self.fail_after:
            raise OSError(5, "Input/output error")

    def close(self):
        self.closed = True
        if self.fail_on_close:
            raise OSError(5, "Input/output error")


class FakeEcu:
    """Just enough EcuData for the logger's engine gate and row builder."""

    def __init__(self, rpm, stale=False):
        self.value_x10 = [0] * config.NUM_PARAMS
        self.value_x10[config.PARAM_RPM] = rpm * 10
        self._stale = stale

    def is_stale(self, param, now):
        return self._stale


class TestWriteFailuresNeverReachTheDisplay(LoggerTestCase):
    """F-02, the highest-value fix in the audit set. The datalogger is the
    least important subsystem here - the README calls it optional - and it
    used to be the one that could kill the most important one, because a
    write error propagated into the unguarded main loop and stopped the dash.
    """

    def setUp(self):
        # These paths report to the serial console on purpose - that is where
        # you diagnose a card fault. Swallow it so a real test failure isn't
        # buried in expected output.
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def running_logger(self, **file_kwargs):
        logger, card = self.make_logger()
        logger._file = FakeFile(**file_kwargs)
        logger._last_write = -10000        # force a row on the next update
        return logger, logger._file

    def test_a_row_is_written_when_all_is_well(self):
        logger, f = self.running_logger()
        logger.update(FakeEcu(3000), 50000)
        self.assertEqual(len(f.lines), 1)
        self.assertEqual(logger.state, datalog.STATE_ON)

    def test_a_write_failure_does_not_propagate(self):
        """If this raises, the main loop catches it and the dash shows a
        fault - for a card the user could simply have nudged."""
        logger, _ = self.running_logger(fail_after=0)
        logger.update(FakeEcu(3000), 50000)      # must not raise

    def test_a_write_failure_turns_logging_off_for_the_boot(self):
        logger, _ = self.running_logger(fail_after=0)
        logger.update(FakeEcu(3000), 50000)
        self.assertEqual(logger.state, datalog.STATE_NO_SD)
        self.assertIsNone(logger._file)

    def test_no_retry_storm_after_a_failure(self):
        """Same policy as a card that was missing at startup: stop trying.
        Retrying an I/O error at 10 Hz for the rest of the drive would stall
        the loop over and over."""
        logger, f = self.running_logger(fail_after=0)
        for tick in range(50000, 60000, 100):
            logger.update(FakeEcu(3000), tick)
        self.assertEqual(len(f.lines), 0)
        self.assertEqual(logger.state, datalog.STATE_NO_SD)

    def test_failure_partway_through_a_session_keeps_what_was_written(self):
        logger, f = self.running_logger(fail_after=3)
        for tick in range(50000, 51000, 100):
            logger.update(FakeEcu(3000), tick)
        self.assertEqual(len(f.lines), 3)
        self.assertEqual(logger.state, datalog.STATE_NO_SD)

    def test_a_close_failure_still_ends_the_session(self):
        """The handle is dropped before close() is attempted, so a failing
        close can't leave a half-dead file that later writes go to."""
        logger, card = self.make_logger()
        logger._file = FakeFile(fail_on_close=True)
        logger._stop()                            # must not raise
        self.assertIsNone(logger._file)

    def test_engine_stopping_closes_the_file(self):
        logger, f = self.running_logger()
        logger.update(FakeEcu(3000), 50000)
        logger.update(FakeEcu(0), 50100)          # key off
        self.assertTrue(f.closed)
        self.assertEqual(logger.state, datalog.STATE_STANDBY)

    def test_stale_rpm_closes_the_file(self):
        """Key-off kills the broadcast, which must also end the session -
        otherwise the last rows are never flushed."""
        logger, f = self.running_logger()
        logger.update(FakeEcu(3000), 50000)
        logger.update(FakeEcu(3000, stale=True), 50100)
        self.assertTrue(f.closed)

    def test_rows_are_rate_limited(self):
        logger, f = self.running_logger()
        logger.update(FakeEcu(3000), 50000)
        logger.update(FakeEcu(3000), 50000 + config.LOG_INTERVAL_MS - 1)
        self.assertEqual(len(f.lines), 1)
        logger.update(FakeEcu(3000), 50000 + config.LOG_INTERVAL_MS)
        self.assertEqual(len(f.lines), 2)

    def test_a_dead_logger_does_nothing_at_all(self):
        logger, _ = self.make_logger()
        logger._unavailable = True
        logger.update(FakeEcu(3000), 50000)       # must not raise or open
        self.assertIsNone(logger._file)


class TestTapZones(unittest.TestCase):
    """Touch X is mapped to a screen column purely to decide left half vs
    right half. The calibration constants are the ones the README tells users
    to swap, so both polarities have to work."""

    def make_nav(self):
        nav = touch.TouchNav.__new__(touch.TouchNav)
        nav._left_zone_max_x = int(config.SCREEN_W * config.TAP_ZONE_FRACTION)
        return nav

    def test_calibration_endpoints_map_to_screen_edges(self):
        nav = self.make_nav()
        self.assertEqual(nav._raw_x_to_screen_x(config.TS_RAW_X_MIN), 0)
        self.assertEqual(nav._raw_x_to_screen_x(config.TS_RAW_X_MAX), config.SCREEN_W - 1)

    def test_clamped_outside_the_calibrated_range(self):
        nav = self.make_nav()
        for raw in (-5000, -1, 0, 4095, 9999):
            sx = nav._raw_x_to_screen_x(raw)
            self.assertTrue(0 <= sx <= config.SCREEN_W - 1)

    def test_whole_adc_domain_stays_on_screen(self):
        nav = self.make_nav()
        for raw in range(0, 4096, 7):
            self.assertTrue(0 <= nav._raw_x_to_screen_x(raw) <= config.SCREEN_W - 1)

    def test_both_calibration_polarities_split_the_screen(self):
        """MIN > MAX (this panel) and MIN < MAX must both give one zone per
        half - swapping them reverses navigation, it doesn't break it."""
        nav = self.make_nav()
        saved = (config.TS_RAW_X_MIN, config.TS_RAW_X_MAX)
        try:
            for lo, hi in ((3760, 250), (250, 3760)):
                config.TS_RAW_X_MIN, config.TS_RAW_X_MAX = lo, hi
                left = nav._raw_x_to_screen_x(lo)
                right = nav._raw_x_to_screen_x(hi)
                self.assertLessEqual(left, nav._left_zone_max_x)
                self.assertGreater(right, nav._left_zone_max_x)
        finally:
            config.TS_RAW_X_MIN, config.TS_RAW_X_MAX = saved


if __name__ == "__main__":
    unittest.main()
