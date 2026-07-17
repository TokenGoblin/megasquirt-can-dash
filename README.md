# MegaSquirt CAN Digital Dash

A CircuitPython digital dash for a MegaSquirt Microsquirt (MS2/Extra) ECU, built on an
Adafruit Feather M4 CAN Express with a 2.4" TFT touchscreen FeatherWing. Ten pages of
color-coded gauges, a race-style RPM shift-light bar, continuous beam bars, a Boost/MAT
beam-gauge page, peak-hold, and CSV datalogging to a physical SD card.

## Why this exists

Commercial CAN dashes for MegaSquirt (AEM, Haltech, etc.) are capable but expensive,
and most hobbyist MegaSquirt/Microsquirt builds don't need everything they offer.
This project's goal is to give the MegaSquirt community a **inexpensive**,
**fully open-source** dash/monitoring option built entirely from off-the-shelf Adafruit
parts (a Feather board and a touchscreen FeatherWing) and free tools - no proprietary
dash hardware, no licensing, no subscription. Everything - protocol decoding, UI,
alerting, datalogging - is one readable Python file you can fork and change to fit
your own build. If you have a MegaSquirt broadcasting CAN data and a soldering iron,
this is meant to get you a full-featured dash for a fraction of the cost of a
commercial unit.

Contributions, forks, and adaptations for other MegaSquirt variants (MS3, different
broadcast configurations, other sensors) are welcome - see
[Known limitations](#known-limitations) for the rough edges that could use more
real-world testing.

## Hardware

- [Adafruit Feather M4 CAN Express](https://www.adafruit.com/product/4759) - native CAN
  peripheral, no separate CAN transceiver board needed
- [Adafruit 2.4" TFT FeatherWing V2](https://www.adafruit.com/product/3315) - ILI9341
  display + TSC2007 resistive touch + onboard microSD slot
  - **Must be V2.** V1 of this wing uses an STMPE610 touch controller, not the TSC2007
    this code talks to.
- MegaSquirt Microsquirt (MS2/Extra firmware) ECU, CAN-capable
- A microSD card (FAT32), optional - only needed if you want datalogging

The FeatherWing stacks directly onto the Feather - no additional wiring required. The
board runs in **portrait** orientation.

## Software setup

1. Flash **CircuitPython 10.x** onto the Feather M4 CAN
   ([download](https://circuitpython.org/board/feather_m4_can/)). Built and tested
   against CircuitPython 10.2.1.
2. Copy `code.py` and `boot.py` from this repo to the root of the `CIRCUITPY` drive.
3. Copy these libraries from the
   [Adafruit CircuitPython Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases)
   (10.x bundle) into `CIRCUITPY/lib/`:
   - `adafruit_display_text/`
   - `adafruit_ili9341.mpy`
   - `adafruit_tsc2007.mpy`
   - `adafruit_sdcard.mpy`
   - `adafruit_bus_device/` (dependency of the above)

   Only copy these four libraries (plus their one dependency) - the internal flash on
   this board is small (~2MB) and shared with your code; dumping the entire bundle in
   will fill it.
4. (Optional) Add your own boot splash - see [Custom splash screen](#custom-splash-screen)
   below.
5. In TunerStudio, enable **Simplified Dash Broadcasting** and set the CAN bus to
   **500 kbps**. Note the **CAN ID (Base)** it's configured to use (default `1512` /
   `0x5E8`) - it must match `BASE_CAN_ID` in `code.py`.

That's it - CircuitPython auto-runs `code.py` on power-up.

## Pages

Tap the left or right half of the screen to move between pages (wraps around at the
ends). A row of dots at the bottom shows which page you're on.

1. **RPM** - with a 12-segment shift-light bar (blue idle / green / yellow / red)
2. **MAP** (kPa) - fill bar and big number both blue under vacuum, green under
   boost (relative to atmospheric pressure)
3. **Boost** (PSI, derived from MAP) - fill bar, flat blue up through 18 PSI
   then fading to red by 21 PSI
4. **Coolant** (°F) - fill bar, blue (<160) / green (160-200) / yellow fading
   to red (200-230)
5. **TPS** (%) - fill bar, blue → green → yellow gradient as the throttle opens
6. **AFR 1** - center-zero bar, 9-18 AFR; the fill grows from 14.7 (stoich) at
   center outward - left when rich, right when lean - tracking the current AFR.
   Two orange markers hold the richest (low) and leanest (high) AFR reached,
   shown below as `LO ##.# HI ##.#`
7. **Battery** (V) - fill bar, 10-16V, blue (normal resting) → green (normal
   charging) → red (low or overcharging); its marker and readout are a running
   average (`AVG ##.#`) rather than a peak, since average system voltage tells
   you more about a battery's health
8. **MAT** - Manifold Air Temp (°F) - fill bar, green → red gradient
9. **Overview** - Boost/Coolant/AFR/Battery in one 2x2 glance
10. **Beam gauges** - Boost and MAT as two continuous VU-meter-style bars, each with
    an orange tick marking the highest value reached since power-on

Every single-gauge page also shows a `PEAK ###` readout - the highest value seen for
that channel since power-on (not reset by navigating pages, only by a power cycle).
Two pages differ: AFR shows both a low and high peak (`LO ##.# HI ##.#`), since how
rich *and* how lean it swung both matter; Battery shows a running average
(`AVG ##.#`) instead of a peak, since average system voltage is the more useful
health indicator.

The top-left corner always shows the datalogger's state (`NO SD` / `LOG OFF` /
`LOG ON`); the top-right corner shows a `NO CAN` warning if that channel hasn't
received a CAN frame in the last second.

## CAN protocol

Base ID defaults to `1512` (`0x5E8`) at 500 kbps, matching TunerStudio's Simplified Dash
Broadcasting default. All fields are big-endian. Byte offsets are cross-checked against
the official *"Megasquirt CAN realtime data broadcast protocol"* document
(EFI Analytics, 2016-02-17):

| Frame (Base+N) | Field | Byte offset | Scale |
|---|---|---|---|
| +0 | MAP (kPa) | 0-1 | ÷10 |
| +0 | RPM | 2-3 | ÷1 |
| +0 | CLT (°F) | 4-5 | ÷10 |
| +0 | TPS (%) | 6-7 | ÷10 |
| +1 | MAT (°F) | 4-5 | ÷10 |
| +2 | AFR1 | 1 (single byte) | ÷10 |
| +3 | Battery (V) | 0-1 | ÷10 |

All of these constants (`BASE_CAN_ID`, `OFS_*`, `SCALE_*`) live at the top of `code.py`
if your firmware differs - sniff the bus (e.g. `candump`) against a known value (MAT
should roughly match ambient temp cold, CLT likewise) if a channel looks wrong.

## Configuration

Everything you're likely to want to tune is a clearly-labeled constant near the top of
`code.py` - no need to hunt through logic. Notably:

- `BASE_CAN_ID` - must match your TunerStudio CAN broadcast base ID
- `ATMOSPHERIC_KPA` - for the Boost (MAP-derived) calculation to read ~0 at idle
- `*_YELLOW_*` / `*_RED_*` / `*_MIN_*` / `*_MAX_*` - all the color-coded alert
  thresholds (CLT, AFR, RPM, Boost, MAT)
- `BATT_LOW_V` / `BATT_COLD_MIN_V` / `BATT_COLD_MAX_V` / `BATT_RUN_MIN_V` /
  `BATT_RUN_MAX_V` / `BATT_HIGH_V` - battery voltage gradient stops: red
  (weak) → blue (normal, engine off, 12.2-12.6V) → green (normal, engine
  running/charging, 13-14.7V) → red (overcharging, above ~15V)
- `MAP_BLEND_BAND_KPA` - width of the blue↔green transition band straddling
  `ATMOSPHERIC_KPA` on the MAP page (vacuum vs. boost)
- `SHIFT_BAR_MAX_RPM`, `BOOST_BLUE_MAX_PSI`, `BOOST_MAX_PSI`, etc. - the bars'
  full-scale ranges and color transition points
- `TS_RAW_X_MIN` / `TS_RAW_X_MAX` - touch calibration; if left/right taps feel
  backwards on your specific panel, swap these two
- `DISPLAY_ROTATION` - if the screen comes up upside-down, try `270` instead of `90`
- `ENGINE_RUNNING_RPM` - the RPM threshold that starts/stops a datalog session
- `LOG_INTERVAL_S` - datalog sample rate (default 10 Hz)

## Datalogging

Logging only runs when there's a physical SD card in the FeatherWing's onboard slot -
it deliberately never writes to the Feather's own internal flash (see the comment
block above the `SD_CS_PIN` constant in `code.py` for why). A session starts the
moment RPM crosses above `ENGINE_RUNNING_RPM` and ends when it drops back below;
each session gets its own file at `/sd/logs/log001.csv`, `log002.csv`, etc.

The file is plain CSV (a name row, then a units row, then data) - **not** a real
MegaLogViewer `.mlg` binary file. See the note at the top of `code.py` for why: MLG is
a proprietary binary format with no way to verify a from-scratch implementation
against the real closed-source parser. MegaLogViewer HD's own delimited-file loader
explicitly supports this CSV format directly, so it opens with no conversion step.

## Custom splash screen

`code.py` will show `/splash.bmp` for 2 seconds at boot if present (and just skips it
silently if it's missing - nothing to configure). To make your own:

1. Any image, resized/cropped to exactly **240x320** (portrait).
2. Convert to a **16-bit RGB565 BMP** - CircuitPython's `displayio.OnDiskBitmap`
   doesn't reliably support plain 24/32-bit true-color BMPs, only indexed (≤8-bit) or
   16-bit. Most simple image editors won't export this directly; a quick way on
   Windows is via PowerShell + `System.Drawing`:

   ```powershell
   Add-Type -AssemblyName System.Drawing
   $src = [System.Drawing.Image]::FromFile("your-image.png")
   $dest = New-Object System.Drawing.Bitmap 240, 320, ([System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
   $g = [System.Drawing.Graphics]::FromImage($dest)
   $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
   $g.DrawImage($src, 0, 0, 240, 320)
   $dest.Save("splash.bmp", [System.Drawing.Imaging.ImageFormat]::Bmp)
   ```
3. Copy `splash.bmp` to the root of `CIRCUITPY`.

## Known limitations

- **MAT byte offset**: cross-checked against the official protocol doc (see table
  above) - should be correct, but verify against your own TunerStudio project if it
  looks wrong (MAT should track ambient air temp with a cold engine).
- **Touch is tap-only, not swipe** - the resistive panel on this FeatherWing isn't
  reliable enough for drag gestures, so navigation is a firm tap on the left/right
  half of the screen instead.
- **No `.mlg` binary output** - see [Datalogging](#datalogging) above.
- Color thresholds (CLT/AFR/Battery/RPM/Boost/MAT) are tuned for a fairly typical
  turbocharged street engine - adjust the constants for your combination.

## License

MIT - see [LICENSE](LICENSE).
