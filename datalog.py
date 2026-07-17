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

import board
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


class DataLogger:
    """Self-contained engine-gated CSV logger.

    Construction attempts the SD mount exactly once; a missing card leaves
    the logger permanently in STATE_NO_SD for this boot (mirroring the
    original behavior - no retry storms against an empty slot).
    """

    def __init__(self, spi):
        """Mount the SD card if present.

        Parameters:
            spi - the SHARED SPI bus object (same bus as the TFT; each
                  driver locks it per transaction, so they coexist safely).
        Side effects: mounts /sd on success; prints the outcome either way.
        """
        self._file = None
        self._last_write = 0            # ticks.ms() of the last row
        self._unavailable = True
        try:
            cs = digitalio.DigitalInOut(config.SD_CS_PIN)
            sdcard = adafruit_sdcard.SDCard(spi, cs)
            vfs = storage.VfsFat(sdcard)
            storage.mount(vfs, "/sd")
            self._unavailable = False
            print("SD card mounted at /sd")
        except Exception as e:  # pylint: disable=broad-except
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

    def _next_log_path(self):
        """Return the next unused /sd/logs/logNNN.csv path (creates the
        directory on first use). Scans existing names so numbering continues
        across power cycles."""
        try:
            os.mkdir(config.LOG_DIR)
        except OSError:
            pass  # already exists
        max_n = 0
        for name in os.listdir(config.LOG_DIR):
            if name.startswith("log") and name.endswith(".csv"):
                try:
                    n = int(name[3:-4])
                except ValueError:
                    continue
                if n > max_n:
                    max_n = n
        return "{}/log{:03d}.csv".format(config.LOG_DIR, max_n + 1)

    def _start(self):
        """Open a new session file and write the two MegaLogViewer header
        rows (channel names, then units). A write failure here marks the
        card unavailable for the rest of this boot rather than retrying on
        every engine start."""
        try:
            path = self._next_log_path()
            self._file = open(path, "w")
            names = ["Time"]
            units = ["s"]
            for p in range(config.NUM_PARAMS):
                names.append(config.PAGES[p][0])
                units.append(config.PAGES[p][1])
            self._file.write(",".join(names) + "\n")
            self._file.write(",".join(units) + "\n")
            self._file.flush()
            print("Datalogging started:", path)
        except OSError as e:
            self._file = None
            self._unavailable = True
            print("Datalogging disabled (SD card write failed):", e)

    def _stop(self):
        """Close the current session file cleanly."""
        if self._file is not None:
            self._file.close()
            self._file = None
            print("Datalogging stopped.")

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
        self._file.write(",".join(fields) + "\n")
        self._file.flush()   # crash-safety: never lose more than one row
