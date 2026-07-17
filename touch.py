# ============================================================================
#  touch.py - TSC2007 resistive touch polling + tap-zone navigation
# ============================================================================
#  Purpose:   Polls the FeatherWing's TSC2007 touch controller on its own
#             50 Hz tick and turns firm presses into page-change deltas
#             (-1 = previous page, +1 = next page).
#  Talks to:  TSC2007 resistive touch controller over I2C (board.I2C()).
#  Fits in:   code.py calls TouchNav.update(now) each loop; a non-zero
#             return is fed to ui.change_page().
#
#  Gesture model - taps, NOT swipes (deliberate): this resistive panel is
#  too clunky for reliable drag gestures, so the original design uses two
#  tap zones - left half = previous, right half = next - firing on the
#  finger-DOWN edge so page changes feel instant. The state machine below
#  (IDLE -> TOUCHING -> back to IDLE on release) exists to make that edge
#  detection explicit and to guarantee a held finger can't repeat-fire.
#
#  Allocation notes: in steady state (no finger) the only I2C traffic is the
#  driver's boolean `touched` read, which allocates nothing. The x/pressure
#  dict the driver builds is read ONCE per physical tap, on the press edge -
#  a handful of bytes per user interaction, nothing per-loop.
# ============================================================================

import board
import adafruit_tsc2007

import config
import ticks

# ==== TOUCH STATE MACHINE STATES ============================================
_IDLE = 0       # no finger down; watching for a press edge
_TOUCHING = 1   # finger held; waiting for release before re-arming


class TouchNav:
    """Tap-zone page navigation off the TSC2007, polled at 50 Hz.

    If the controller isn't found (e.g. bare Feather on the bench, or a V1
    FeatherWing with the older STMPE610 chip), construction logs the problem
    and every update() call becomes a no-op - the dash still runs, just
    without navigation. That mirrors the original single-file behavior.
    """

    def __init__(self):
        """Probe the TSC2007 on the shared I2C bus. Never raises."""
        self._touch = None
        try:
            self._touch = adafruit_tsc2007.TSC2007(board.I2C())
        except Exception as e:  # pylint: disable=broad-except
            print("TSC2007 touch controller not found - tap navigation disabled:", e)

        self._state = _IDLE
        self._last_poll = ticks.ms()
        # Precompute the zone boundary once - screen x at or left of this is
        # "previous page".
        self._left_zone_max_x = int(config.SCREEN_W * config.TAP_ZONE_FRACTION)

    def _raw_x_to_screen_x(self, raw_x):
        """Map a raw 12-bit panel X reading to a 0..SCREEN_W-1 pixel column.

        The calibration endpoints come from config (TS_RAW_X_MIN at the
        physical left edge, TS_RAW_X_MAX at the right); on this panel raw X
        runs BACKWARD (decreases left-to-right) and this linear map handles
        either polarity. Only zone classification uses the result, so
        single-pixel accuracy is irrelevant.
        """
        sx = (raw_x - config.TS_RAW_X_MIN) * config.SCREEN_W // (
            config.TS_RAW_X_MAX - config.TS_RAW_X_MIN
        )
        if sx < 0:
            sx = 0
        elif sx > config.SCREEN_W - 1:
            sx = config.SCREEN_W - 1
        return sx

    def update(self, now):
        """Poll the panel if the 50 Hz tick has elapsed; classify any press.

        Parameters: now - this loop pass's ticks.ms() stamp.
        Returns: -1 (left-zone tap), +1 (right-zone tap), or 0 (no event).
        Timing: returns immediately (no I2C) between ticks; one boolean I2C
        read per tick otherwise. Fires exactly once per physical press, on
        the press edge.
        """
        if self._touch is None:
            return 0
        # 50 Hz gate: a deliberate tap lasts >>20 ms, so nothing is missed,
        # and the I2C bus isn't hammered on every single loop pass.
        if ticks.diff(now, self._last_poll) < config.TOUCH_POLL_MS:
            return 0
        self._last_poll = now

        # Boolean pressed/not-pressed read - the cheap, allocation-free poll.
        pressed = self._touch.touched

        if self._state == _TOUCHING:
            if not pressed:
                self._state = _IDLE   # finger lifted - re-arm for the next tap
            return 0

        # _IDLE: look for a press edge.
        if not pressed:
            return 0

        # Press edge: NOW read the full point (the one dict allocation per
        # tap) to get X for zone classification, and re-check pressure
        # against the configurable threshold so users can demand a firmer
        # press than the driver's built-in minimum.
        point = self._touch.touch
        if point is None or point["pressure"] <= config.TOUCH_PRESSURE_THRESHOLD:
            return 0   # too light to count; stay IDLE and let it retry

        self._state = _TOUCHING
        sx = self._raw_x_to_screen_x(point["x"])
        if sx <= self._left_zone_max_x:
            return -1   # left half tapped -> previous page
        return 1        # right half tapped -> next page
