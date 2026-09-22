# ============================================================================
#  canbus.py - MegaSquirt CAN receive, decode, and live-data store
# ============================================================================
#  Purpose:   Owns the SAME51's native CAN peripheral (via canio), decodes
#             the four MegaSquirt "Simplified Dash Broadcasting" frames, and
#             maintains EcuData - the fixed-point live/peak/low/average store
#             every other module reads from.
#  Talks to:  Feather M4 CAN's on-chip CAN controller + onboard transceiver
#             (board.CAN_TX/CAN_RX, standby + boost-enable control pins).
#  Fits in:   code.py calls CanBus.update(ecu, now) once per loop; ui.py and
#             datalog.py read the shared EcuData instance it fills.
#
#  Data model: every channel is stored as an INTEGER at 10x its real value
#  ("x10 fixed-point") exactly as it arrives on the wire - MegaSquirt already
#  broadcasts kPa*10 / degF*10 / etc. Keeping the raw integers means:
#    * no float conversion per frame (decode is integer-only except boost),
#    * change detection is exact int comparison (drives the render-only-on-
#      change logic in ui.py),
#    * strings are only ever built at render/log time, from ints.
# ============================================================================

import struct

import board
import canio
import digitalio

import config
import ticks

# ==== PRECOMPILED STRUCT FORMATS ============================================
# Compile-once format strings ('>' = big-endian / Motorola order, matching
# the MegaSquirt broadcast). Kept as module constants so the hot receive
# path never rebuilds them.
#
# Frame base+0 is four packed 16-bit words: MAP(u16) RPM(u16) CLT(s16)
# TPS(s16). Temperatures are signed per the protocol doc so sub-zero
# weather decodes correctly; MAP/RPM can never be negative.
_FMT_DASH0 = ">HHhh"
_FMT_S16 = ">h"   # single signed word (MAT, battery)

# Frame IDs derived once from the configured base.
_ID_DASH0 = config.BASE_CAN_ID + 0
_ID_DASH1 = config.BASE_CAN_ID + 1
_ID_DASH2 = config.BASE_CAN_ID + 2
_ID_DASH3 = config.BASE_CAN_ID + 3

# Boost conversion: (MAP_x10 - baro_x10) is kPa*10 above atmospheric;
# multiplying kPa by 1/6.894757 gives PSI, so the same factor maps
# kPa*10 -> PSI*10 directly. One float multiply per DASH0 frame
# (~10-30 Hz) - everything else in the decode path is integer math.
_PSI_PER_KPA = 1.0 / config.KPA_PER_PSI

# Barometric reference plumbing (see config.py's barometric section for the
# full design rationale). All x10 fixed-point ints.
_BARO_OVERRIDE_X10 = (
    None if config.ATMOSPHERIC_KPA_OVERRIDE is None
    else int(config.ATMOSPHERIC_KPA_OVERRIDE * 10 + 0.5)
)
_BARO_FALLBACK_X10 = int(config.BARO_FALLBACK_KPA * 10 + 0.5)
_BARO_MIN_X10 = int(config.BARO_MIN_KPA * 10 + 0.5)
_BARO_MAX_X10 = int(config.BARO_MAX_KPA * 10 + 0.5)
_BARO_SHIFT = config.BARO_FILTER_SHIFT


# ==== FIXED-POINT FORMATTING ================================================

def format_x10(v_x10, decimals):
    """Format an x10 fixed-point int as its display string.

    Parameters:
        v_x10    - value * 10 as an int (e.g. 1755 for 175.5), or None.
        decimals - 0 or 1 (from config.PAGES).
    Returns: str ("176" / "175.5" / "---" for None). Allocates the returned
    string - callers must only invoke this when the value actually changed
    (that gating is the core of the zero-allocation steady state).
    """
    if v_x10 is None:
        return "---"
    if decimals == 0:
        # Round-half-up to whole units, symmetric for negatives.
        if v_x10 >= 0:
            return str((v_x10 + 5) // 10)
        return str(-((-v_x10 + 5) // 10))
    # One decimal place: split the fixed-point int - exact, no float round-trip.
    if v_x10 >= 0:
        return "{}.{}".format(v_x10 // 10, v_x10 % 10)
    n = -v_x10
    return "-{}.{}".format(n // 10, n % 10)


# ==== LIVE DATA STORE =======================================================

class EcuData:
    """Fixed-point live values + per-channel timestamps and statistics.

    All channels indexed by config.PARAM_* (0..NUM_PARAMS-1). Every store is
    preallocated here at startup; set() only ever mutates ints in place, so
    steady-state updates allocate nothing.

    Side effects/timing: set() also maintains peak / low / running-average,
    so those never need a separate pass.
    """

    def __init__(self):
        n = config.NUM_PARAMS
        self.value_x10 = [0] * n       # latest value, x10 fixed-point
        self.updated = [None] * n      # ticks.ms() of last update, or None = never
        self.peak_x10 = [None] * n     # highest since power-on (PEAK readouts)
        self.low_x10 = [None] * n      # lowest since power-on (AFR "LO")
        self.avg_sum = [0] * n         # running-average accumulators (Battery "AVG")
        self.avg_count = [0] * n

    def set(self, param, v_x10, now):
        """Store a fresh sample for `param` stamped at tick `now`.

        Updates live value, freshness timestamp, peak, low, and average
        accumulators in one pass. Integer-only; allocation-free.
        """
        self.value_x10[param] = v_x10
        self.updated[param] = now
        pk = self.peak_x10[param]
        if pk is None or v_x10 > pk:
            self.peak_x10[param] = v_x10
        lo = self.low_x10[param]
        if lo is None or v_x10 < lo:
            self.low_x10[param] = v_x10
        self.avg_sum[param] += v_x10
        self.avg_count[param] += 1

    def is_stale(self, param, now):
        """True if `param` has never updated or hasn't within the stale
        timeout. Peaks/low/average deliberately survive staleness - they only
        reset on a power cycle."""
        last = self.updated[param]
        if last is None:
            return True
        return ticks.diff(now, last) > config.STALE_TIMEOUT_MS

    def average_x10(self, param):
        """Running mean since power-on as an x10 int (round-half-up), or
        None if no samples yet."""
        cnt = self.avg_count[param]
        if cnt == 0:
            return None
        return (self.avg_sum[param] + cnt // 2) // cnt


# ==== CAN BUS ===============================================================

class CanBus:
    """Hardware-filtered, non-blocking MegaSquirt CAN receiver.

    Raises whatever canio.CAN() raises if the peripheral can't initialize
    (code.py catches that and shows the fatal screen). After construction,
    update() never blocks and never raises in normal operation.
    """

    def __init__(self):
        """Bring the transceiver out of standby and open a filtered listener.

        The Feather M4 CAN's onboard TJA1051 transceiver powers up in standby
        and is fed by a switchable 5V boost converter - BOTH must be enabled
        or the controller sees a dead bus.
        """
        if hasattr(board, "CAN_STANDBY"):
            standby = digitalio.DigitalInOut(board.CAN_STANDBY)
            standby.switch_to_output(value=False)   # LOW = transceiver active

        if hasattr(board, "BOOST_ENABLE"):
            boost = digitalio.DigitalInOut(board.BOOST_ENABLE)
            boost.switch_to_output(value=True)      # HIGH = 5V rail on

        # auto_restart=True lets the controller recover from bus-off (e.g. a
        # transient wiring short) by itself instead of staying dead until a
        # power cycle - the stale-data UI covers the gap meanwhile.
        self._can = canio.CAN(
            tx=board.CAN_TX, rx=board.CAN_RX,
            baudrate=config.CAN_BAUD_RATE, auto_restart=True,
        )

        # One exact-ID hardware filter per dash frame: the SAME51 accepts
        # ONLY these four IDs into its receive FIFO, so Python never spends a
        # microsecond receiving-and-discarding unrelated traffic no matter
        # how busy the bus is.
        matches = [
            canio.Match(_ID_DASH0),
            canio.Match(_ID_DASH1),
            canio.Match(_ID_DASH2),
            canio.Match(_ID_DASH3),
        ]
        # timeout=0: receive() returns immediately (None when the FIFO is
        # empty) - the non-blocking contract the main loop depends on.
        self._listener = self._can.listen(matches=matches, timeout=0)

        # Barometric reference, x10 kPa. With an override configured this is
        # pinned for the whole run; otherwise it starts unlocked (None) and
        # is captured/tracked from engine-off MAP frames in _decode().
        self._baro_x10 = _BARO_OVERRIDE_X10

        # ticks.ms() of the most recently received dash frame (None = never).
        # Drives the NeoPixel activity light in statusled.py.
        self.last_rx = None

    @property
    def baro_ref_x10(self):
        """Current barometric reference (kPa * 10 int) used for the Boost
        derivation and the MAP page's vacuum/boost color boundary: the
        configured override, else the captured engine-off value, else the
        sea-level fallback until the first valid capture."""
        b = self._baro_x10
        return _BARO_FALLBACK_X10 if b is None else b

    @property
    def bus_ok(self):
        """True while the controller is in a working error state
        (ERROR_ACTIVE or ERROR_WARNING). False in ERROR_PASSIVE/BUS_OFF -
        ui.py raises the NO CAN banner immediately on that, without waiting
        out the per-channel stale timeout."""
        state = self._can.state
        return (state == canio.BusState.ERROR_ACTIVE
                or state == canio.BusState.ERROR_WARNING)

    def update(self, ecu, now):
        """Drain every pending frame into `ecu`. Called once per main loop.

        Parameters:
            ecu - the shared EcuData store.
            now - this loop pass's ticks.ms() stamp (applied to all frames
                  drained this pass; they arrived within the last ms anyway).

        Loops until the FIFO is empty so bursts can never back up, bounded
        by CAN_MAX_FRAMES_PER_UPDATE to cap worst-case loop time. Never
        blocks (listener timeout=0).
        """
        remaining = config.CAN_MAX_FRAMES_PER_UPDATE
        while remaining:
            msg = self._listener.receive()
            if msg is None:
                return                      # FIFO empty - done this pass
            remaining -= 1
            self.last_rx = now
            data = getattr(msg, "data", None)
            if data is None or len(data) < 8:
                continue                    # RTR frame / runt - no payload to decode
            self._decode(msg.id, data, ecu, now)

    # ---- frame decoding ----------------------------------------------------

    def _decode(self, msg_id, data, ecu, now):
        """Decode one 8-byte broadcast frame into the EcuData store.

        Byte layouts cite the official "Megasquirt CAN realtime data
        broadcast protocol" doc (2016-02-17, EFI Analytics). All values land
        as x10 ints - the only float op is the boost (kPa -> PSI) derive.
        """
        if msg_id == _ID_DASH0:
            # base+0: MAP kPa*10 | RPM | CLT degF*10 | TPS %*10 - one unpack
            # for all four words.
            map_x10, rpm, clt_x10, tps_x10 = struct.unpack(_FMT_DASH0, data)
            ecu.set(config.PARAM_MAP, map_x10, now)
            # RPM arrives x1; store x10 like everything else so formatting
            # and change-detection stay uniform (70000 is still a small int).
            ecu.set(config.PARAM_RPM, rpm * 10, now)
            ecu.set(config.PARAM_CLT, clt_x10, now)
            ecu.set(config.PARAM_TPS, tps_x10, now)

            # Baro capture: engine-off MAP *is* local barometric pressure.
            # Gated on RPM == 0 (same frame - can never mistake idle vacuum
            # for atmosphere) plus a plausibility window (rejects garbage
            # frames from a booting ECU). First qualifying frame locks the
            # reference exactly; later engine-off frames low-pass toward MAP
            # so a stop at a different altitude quietly recalibrates. Frozen
            # whenever the engine runs. Integer-only, allocation-free.
            if _BARO_OVERRIDE_X10 is None and rpm == 0 and (
                _BARO_MIN_X10 <= map_x10 <= _BARO_MAX_X10
            ):
                b = self._baro_x10
                if b is None:
                    self._baro_x10 = map_x10
                elif b != map_x10:
                    step = (map_x10 - b) >> _BARO_SHIFT
                    if step == 0 and map_x10 > b:
                        step = 1  # floor-shift stalls on small +deltas; nudge
                    self._baro_x10 = b + step

            # Boost is derived from MAP (gauge pressure above the baro
            # reference, in PSI) and stamped with the same tick so it goes
            # stale exactly when MAP does. Round-half-away-from-zero to the
            # nearest 0.1 PSI.
            ref = self._baro_x10
            if ref is None:
                ref = _BARO_FALLBACK_X10
            b = (map_x10 - ref) * _PSI_PER_KPA
            ecu.set(config.PARAM_BOOST,
                    int(b + 0.5) if b >= 0 else int(b - 0.5), now)

        elif msg_id == _ID_DASH1:
            # base+1: MAT degF*10 is the third word (offset 4); words 0/1 are
            # pw1/pw2 (injector pulse widths - not displayed).
            (mat_x10,) = struct.unpack_from(_FMT_S16, data, config.OFS_MAT)
            ecu.set(config.PARAM_MAT, mat_x10, now)

        elif msg_id == _ID_DASH2:
            # base+2: AFR1 is a SINGLE unsigned byte at offset 1 (offset 0 is
            # afrtgt1, the target AFR). No struct needed for one byte, and the
            # byte is already AFR*10 - store it as-is. An earlier bug read
            # bytes 0-1 as one word - see the protocol doc.
            ecu.set(config.PARAM_AFR, data[config.OFS_AFR1], now)

        elif msg_id == _ID_DASH3:
            # base+3: battery voltage V*10 in the first word.
            (batt_x10,) = struct.unpack_from(_FMT_S16, data, config.OFS_BATT)
            ecu.set(config.PARAM_BATT, batt_x10, now)
        # Anything else (there shouldn't be - hardware filters) is ignored.
