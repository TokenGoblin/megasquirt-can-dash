# ============================================================================
#  statusled.py - onboard NeoPixel as an Ethernet-style CAN link/activity LED
# ============================================================================
#  Purpose:   Flickers the Feather's single NeoPixel while dash frames are
#             arriving, the way a network port's link light blinks with
#             traffic. While the bus is quiet it slowly "breathes" blue ->
#             purple. While a datalog session is writing to the SD card, an
#             aircraft-strobe-style yellow double flash is laid over the
#             top. Solid error color while the CAN controller is
#             error-passive / bus-off (overrides everything).
#  Talks to:  The Feather M4 CAN's onboard NeoPixel (board.NEOPIXEL, power
#             gated by board.NEOPIXEL_POWER) via the built-in neopixel_write
#             module - no library in CIRCUITPY/lib needed.
#  Fits in:   code.py calls StatusLed.update(now, can.last_rx, can.bus_ok,
#             logging) once per loop; it self-schedules off config.LED_TICK_MS.
#
#  Every pattern is a pure function of `now`, so nothing drifts and the tick
#  rate only sets timing resolution. Cost: one int compare per loop between
#  ticks, a little integer math per tick, and a pixel write (~30 us) only
#  when the color actually changes. Never allocates.
# ============================================================================

import board
import digitalio
import neopixel_write

import config
import ticks


def _split(rgb):
    """0xRRGGBB -> (r, g, b) ints."""
    return (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF


def _scaled(rgb, brightness):
    """0xRRGGBB at brightness (0.0-1.0) -> (r, g, b) ints. Startup-only."""
    s = int(brightness * 1000 + 0.5)
    return tuple(c * s // 1000 for c in _split(rgb))


class StatusLed:
    """Link/activity indicator. Degrades to a no-op on boards without a
    NeoPixel (or with config.LED_ENABLED = False)."""

    def __init__(self):
        self._pin = None
        if not config.LED_ENABLED or not hasattr(board, "NEOPIXEL"):
            return
        if hasattr(board, "NEOPIXEL_POWER"):
            # Keep a reference so the pin (and the pixel's power) stays on.
            self._power = digitalio.DigitalInOut(board.NEOPIXEL_POWER)
            self._power.switch_to_output(value=True)
        self._pin = digitalio.DigitalInOut(board.NEOPIXEL)
        self._pin.direction = digitalio.Direction.OUTPUT

        self._act = _scaled(config.LED_ACTIVITY_COLOR, config.LED_BRIGHTNESS)
        self._err = _scaled(config.LED_ERROR_COLOR, config.LED_BRIGHTNESS)
        self._log = _scaled(config.LED_LOG_COLOR, config.LED_LOG_BRIGHTNESS)
        self._idle_a = _split(config.LED_IDLE_COLOR_A)
        self._idle_b = _split(config.LED_IDLE_COLOR_B)
        # Integer per-mille so the breathing math stays int-only.
        self._idle_max = int(config.LED_IDLE_MAX_BRIGHTNESS * 1000 + 0.5)

        # Strobe windows within one LED_LOG_PERIOD_MS cycle: flash, gap, flash.
        f = config.LED_LOG_FLASH_MS
        self._f1_end = f
        self._f2_start = f + config.LED_LOG_GAP_MS
        self._f2_end = self._f2_start + f

        self._buf = bytearray(3)  # GRB wire order, reused for every write
        self._written = False
        self._next = ticks.ms()
        self._show(0, 0, 0)

    def _show(self, r, g, b):
        buf = self._buf
        if self._written and buf[0] == g and buf[1] == r and buf[2] == b:
            return
        buf[0] = g
        buf[1] = r
        buf[2] = b
        neopixel_write.neopixel_write(self._pin, buf)
        self._written = True

    def _breathe(self, now):
        """Idle: triangle-wave brightness 0 -> max -> 0 over one period, with
        the color sliding A -> B on the way up and back on the way down."""
        period = config.LED_IDLE_BREATHE_MS
        p = now % period             # one tiny glitch at the ~6-day tick wrap
        half = period >> 1
        t = p * 1000 // half if p < half else (period - p) * 1000 // half
        k = t * self._idle_max       # brightness * 1e6
        a = self._idle_a
        z = self._idle_b
        self._show(
            (a[0] + (z[0] - a[0]) * t // 1000) * k // 1000000,
            (a[1] + (z[1] - a[1]) * t // 1000) * k // 1000000,
            (a[2] + (z[2] - a[2]) * t // 1000) * k // 1000000,
        )

    def update(self, now, last_rx, bus_ok, logging):
        """Advance the LED state machine.

        Parameters:
            now     - this loop pass's ticks.ms() stamp.
            last_rx - ticks.ms() of the most recent dash frame, or None.
            bus_ok  - CanBus.bus_ok (False = error-passive / bus-off).
            logging - True while a datalog session is writing to the SD card.
        """
        if self._pin is None or ticks.diff(now, self._next) < 0:
            return
        self._next = now + config.LED_TICK_MS

        if not bus_ok:
            c = self._err
            self._show(c[0], c[1], c[2])
            return

        if logging:
            p = now % config.LED_LOG_PERIOD_MS
            if p < self._f1_end or self._f2_start <= p < self._f2_end:
                c = self._log
                self._show(c[0], c[1], c[2])
                return

        if last_rx is not None and ticks.diff(now, last_rx) < config.LED_ACTIVITY_MS:
            # Traffic within the window: alternate every LED_BLINK_MS ->
            # steady flicker at 1000 / (2 * LED_BLINK_MS) Hz.
            if (now // config.LED_BLINK_MS) & 1:
                c = self._act
                self._show(c[0], c[1], c[2])
            else:
                self._show(0, 0, 0)
        else:
            self._breathe(now)
