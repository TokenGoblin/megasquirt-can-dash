# ============================================================================
#  datalog.py - engine-gated CSV datalogger (physical SD card only)
# ============================================================================
#  Purpose:   Logs every channel to numbered CSV files on the FeatherWing's
#             microSD slot at 10 Hz, automatically starting a session when
#             the engine starts (RPM threshold) and closing it when it stops.
#  Talks to:  microSD card over the shared SPI bus (chip-select board.D5),
#             via adafruit_sdcard + storage.VfsFat mounted at /sd.
#  Fits in:   code.py calls DataLogger.update(ecu, now) once per loop; ui.py
#             reads .state to draw the LOG ON / LOG OFF / NO SD corner text.
#
#  Why a physical SD card and never internal flash: the CIRCUITPY flash is
#  ~2 MB, shared with the dash's own code/libraries, and writing it from
#  code forces it read-only to the host PC (the "media is write protected"
#  lockout). The SD card mounts at its own path, has real capacity, and a
#  missing card simply disables logging - it can never affect the display.
#
#  Why CSV and not MegaLogViewer's .mlg: MLG is a proprietary binary format
#  with no way to verify a from-scratch writer against the closed-source
#  parser - one wrong header byte makes a file it silently refuses to open.
#  MegaLogViewer HD's delimited-file loader documents exactly this CSV shape
#  (a name row, then a units row, then data), so these files open directly.
#
#  Allocation notes: while a session is active this module builds one row
#  string per LOG_INTERVAL_MS (10 Hz) - unavoidable for file I/O, and it
#  only happens while the engine is running. flush() after every row costs
#  a little throughput but means a yanked power feed (key-off) loses at most
#  one row - the right trade in a car.
# ============================================================================

import os
import time

import digitalio
import storage
import adafruit_sdcard

import config
import ticks
from canbus import format_x10

# ==== LOGGER STATES (read by ui.py for the corner indicator) ================
STATE_NO_SD = 0     # no card / card failed - logging off for this boot
STATE_STANDBY = 1   # card ready, engine not running - between sessions
STATE_ON = 2        # session file open, rows being written

# Upper bound on the file-number probe (see _first_free_index). A card holding
# this many sessions is far past the point of wanting a cleanup; the cap only
# exists so a pathological directory can't spin the search forever.
_MAX_LOG_INDEX = 1 << 16


class DataLogger:
    """Self-contained engine-gated CSV logger.

    Construction attempts the SD mount exactly once; a missing card leaves
    the logger permanently in STATE_NO_SD for this boot (mirroring the
    original behavior - no retry storms against an empty slot).
    """

    def __init__(self, spi):
        """Mount the SD card if present and pick the first free file number.

        Parameters:
            spi - the SHARED SPI bus object (same bus as the TFT; each
                  driver locks it per transaction, so they coexist safely).
        Side effects: mounts /sd and creates LOG_DIR on success; prints the
        outcome either way.

        All the card work that can be done once is done HERE, at boot, where
        a stall costs nothing - never at engine start, where a stall costs the
        driver their gauges.
        """
        self._file = None
        self._last_write = 0            # ticks.ms() of the last row
        self._unavailable = True
        self._next_n = 1                # next session file number to try
        try:
            cs = digitalio.DigitalInOut(config.SD_CS_PIN)
            sdcard = adafruit_sdcard.SDCard(spi, cs)
            vfs = storage.VfsFat(sdcard)
            storage.mount(vfs, "/sd")
            self._next_n = self._prepare_log_dir()
            self._unavailable = False
            print("SD card mounted at /sd; next log file:", self._log_path(self._next_n))
        except Exception as e:  # pylint: disable=broad-except
            # Deliberately broad: a card that mounts but whose directory can't
            # be read or created is just as unusable, and NO failure in here
            # may reach the caller - the display must come up regardless.
            print("No SD card detected - datalogging disabled:", e)

    @property
    def state(self):
        """Current logger state: STATE_NO_SD / STATE_STANDBY / STATE_ON."""
        if self._unavailable:
            return STATE_NO_SD
        if self._file is not None:
            return STATE_ON
        return STATE_STANDBY

    # ---- session management --------------------------------------------------

    @staticmethod
    def _log_path(n):
        """Session file path for number `n`."""
        return "{}/log{:03d}.csv".format(config.LOG_DIR, n)

    def _exists(self, n):
        """True if session file `n` is already on the card. One stat, no
        allocation worth speaking of."""
        try:
            os.stat(self._log_path(n))
            return True
        except OSError:
            return False

    def _prepare_log_dir(self):
        """Create LOG_DIR if needed and return the first free file number.

        Deliberately does NOT call os.listdir(): that builds a Python list of
        every filename in the directory, which grows by one per engine start
        forever and eventually can't fit in the ~24 KB of free heap this board
        runs on (the resulting MemoryError used to land at engine start and
        take the whole dash with it). Instead: double until a number is free,
        then bisect. That's ~2*log2(N) stat calls - about 20 for a card
        holding a thousand sessions, and it happens once per boot rather than
        once per session.

        Numbering continues across power cycles, same as before, and on an
        untouched card the result is identical to the old scan (highest + 1).
        The one behavior change: if you DELETE some logs and leave others, the
        bisect can hand back a low free number rather than continuing past the
        highest file, so new sessions may fill the gap instead of appending
        after it. Finding the true maximum is precisely the full-directory
        scan this replaces. Nothing is ever overwritten - every returned
        number is confirmed free, here and again in _first_free_index() - so
        the cost is out-of-order file names on a hand-pruned card, never data.
        """
        try:
            os.mkdir(config.LOG_DIR)
        except OSError:
            pass  # already exists

        hi = 1
        while hi <= _MAX_LOG_INDEX and self._exists(hi):
            hi *= 2
        # Invariant from here: `hi` is free, `lo` is taken (or lo == 0).
        lo = hi // 2
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self._exists(mid):
                lo = mid
            else:
                hi = mid
        return hi

    def _first_free_index(self):
        """Confirm the remembered number is still free, stepping past anything
        that isn't. Normally exactly one stat: the bookkeeping in _start()
        keeps _next_n correct, so this is a cheap guard against the edge cases
        (a card past _MAX_LOG_INDEX, files added behind our back) rather than
        a search."""
        n = self._next_n
        while n <= _MAX_LOG_INDEX and self._exists(n):
            n += 1
        return n

    def _start(self):
        """Open a new session file and write the two MegaLogViewer header
        rows (channel names, then units). A failure here marks the card
        unavailable for the rest of this boot rather than retrying on every
        engine start."""
        try:
            n = self._first_free_index()
            path = self._log_path(n)
            self._file = open(path, "w")
            names = ["Time"]
            units = ["s"]
            for p in range(config.NUM_PARAMS):
                names.append(config.PAGES[p][0])
                units.append(config.PAGES[p][1])
            self._file.write(",".join(names) + "\n")
            self._file.write(",".join(units) + "\n")
            self._file.flush()
            self._next_n = n + 1
            print("Datalogging started:", path)
        except Exception as e:  # pylint: disable=broad-except
            # Broad on purpose: OSError is the expected failure, but anything
            # raised here (MemoryError, a driver quirk) must end as "logging
            # off" rather than as an exception climbing into the main loop.
            self._file = None
            self._unavailable = True
            print("Datalogging disabled (session could not be started):", e)

    def _stop(self):
        """Close the current session file. A close that fails still ends the
        session - the handle is dropped first so a half-dead file can never be
        written to again."""
        if self._file is None:
            return
        f = self._file
        self._file = None
        try:
            f.close()
            print("Datalogging stopped.")
        except OSError as e:
            self._unavailable = True
            print("Datalogging disabled (SD card close failed):", e)

    # ---- main-loop entry point -------------------------------------------------

    def update(self, ecu, now):
        """Run the engine-gate and, if a session is open, write a row at the
        configured interval.

        Parameters:
            ecu - shared EcuData store (RPM gate + row values).
            now - this loop pass's ticks.ms() stamp.
        Timing: no-op in STATE_NO_SD. In a session, at most one row build +
        SD write + flush per LOG_INTERVAL_MS; SD flush latency (typ. a few
        ms, occasionally tens) is the one place the loop stalls briefly -
        accepted for crash-proof logs, and only while the engine runs.
        """
        if self._unavailable:
            return

        # Engine gate: session starts the moment live RPM rises above the
        # threshold and stops when it drops back (or the RPM channel goes
        # stale - key-off kills the broadcast, which must also close the file).
        running = (not ecu.is_stale(config.PARAM_RPM, now)) and (
            ecu.value_x10[config.PARAM_RPM] > config.ENGINE_RUNNING_RPM * 10
        )
        if running and self._file is None:
            self._start()
        elif not running and self._file is not None:
            self._stop()

        if self._file is None:
            return

        if ticks.diff(now, self._last_write) < config.LOG_INTERVAL_MS:
            return
        self._last_write = now

        # Row: wall-clock-ish Time column (seconds since power-on; only the
        # log uses time.monotonic() - all scheduling runs on ticks) followed
        # by every channel, blank when stale so gaps are visible in the log.
        fields = ["{:.2f}".format(time.monotonic())]
        for p in range(config.NUM_PARAMS):
            if ecu.is_stale(p, now):
                fields.append("")
            else:
                fields.append(format_x10(ecu.value_x10[p], config.PAGES[p][2]))
        try:
            self._file.write(",".join(fields) + "\n")
            self._file.flush()   # crash-safety: never lose more than one row
        except OSError as e:
            # A card yanked, full, or gone flaky mid-drive costs the log and
            # NOTHING else. Drop the handle (closing it would most likely
            # raise again) and stay off for the rest of this boot, exactly as
            # a card that was missing at startup does - the UI falls back to
            # NO SD and the driver keeps their gauges.
            self._file = None
            self._unavailable = True
            print("Datalogging disabled (SD card write failed):", e)
