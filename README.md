# MegaSquirt CAN Digital Dash

A CircuitPython digital dash for a MegaSquirt Microsquirt (MS2/Extra) ECU, built on an
Adafruit Feather M4 CAN Express with a 2.4" TFT touchscreen FeatherWing. Ten pages of
color-coded gauges, a race-style RPM shift-light bar, continuous beam bars with
peak-hold, a Boost/MAT beam-gauge page, and CSV datalogging to a microSD card.

## Why this exists

Commercial CAN dashes for MegaSquirt (AEM, Haltech, etc.) are capable but expensive,
and most hobbyist MegaSquirt/Microsquirt builds don't need everything they offer.
This project's goal is to give the MegaSquirt community an **inexpensive**,
**fully open-source** dash/monitoring option built entirely from off-the-shelf Adafruit
parts (a Feather board and a touchscreen FeatherWing) and free tools - no proprietary
dash hardware, no licensing, no subscription. Everything - protocol decoding, UI,
alerting, datalogging - is a few readable Python files you can fork and change to fit
your own build. If you have a MegaSquirt broadcasting CAN data and a soldering iron,
this is meant to get you a full-featured dash for a fraction of the cost of a
commercial unit.

Contributions, forks, and adaptations for other MegaSquirt variants (MS3, different
broadcast configurations, other sensors) are welcome - see
[Known limitations](#known-limitations) for the rough edges that could use more
real-world testing.

## Features

- **8 single-gauge pages** - RPM, MAP, Boost (derived from MAP), Coolant, TPS,
  AFR, Battery, MAT - each with a big color-coded number, units, and a graphic bar
- **RPM shift-light bar** - 7 round "LED" bulbs sweeping blue → green → yellow → red
- **Continuous fill bars** with smooth color gradients and orange peak-hold markers
- **AFR center-zero bar** - fill grows outward from 14.7 stoich (left = rich,
  right = lean) with richest/leanest hold markers and a `LO/HI` readout
- **Battery running-average** marker and `AVG` readout (more useful than a peak)
- **Self-calibrating boost zero** - baro reference captured from engine-off MAP
  (RPM-gated + plausibility-checked), so Boost reads 0.0 engine-off at any
  altitude, in any weather, out of the box
- **Overview page** - Boost / Coolant / AFR / Battery in one 2x2 glance
- **Beam-gauge page** - Boost + MAT as two big VU-meter-style bars
- **Peak-hold everywhere** - highest value since power-on, per channel
- **Stale-data safety** - any channel silent for >1 s shows `---` and a `NO CAN`
  banner (also raised instantly if the CAN controller reports a failed bus state)
- **Engine-gated CSV datalogging** to microSD, MegaLogViewer HD compatible
- **Tap navigation** - left half = previous page, right half = next page
- **Custom boot splash** from a BMP on the CIRCUITPY drive
- Strictly **non-blocking** cooperative main loop; see [PERFORMANCE.md](PERFORMANCE.md)

## Hardware

| Part | Notes |
|---|---|
| [Adafruit Feather M4 CAN Express](https://www.adafruit.com/product/4759) | SAME51 with native CAN peripheral + onboard transceiver - no separate CAN board needed |
| [Adafruit 2.4" TFT FeatherWing **V2**](https://www.adafruit.com/product/3315) | ILI9341 display + TSC2007 resistive touch + onboard microSD slot. **Must be V2** - V1 uses an STMPE610 touch chip this code doesn't speak |
| MegaSquirt Microsquirt (MS2/Extra firmware) | Any CAN-capable MegaSquirt broadcasting "Simplified Dash Broadcasting" should work; developed against a Microsquirt |
| microSD card, FAT32 (optional) | Only needed for datalogging |

The FeatherWing stacks directly onto the Feather - no wiring between them. The
display runs in **portrait** orientation.

## Wiring and CAN termination

Only two signal wires connect the dash to the car:

- **CANH / CANL** on the Feather M4 CAN's screw-terminal pads → the Microsquirt's
  CANH / CANL. Use twisted-pair wire.
- **Power**: feed the Feather clean switched 12V through a 12V→5V (or USB) supply
  into its USB or BAT input. Don't power it from an unfiltered ignition feed.

**Termination:** a CAN bus needs **120 Ω at each physical end** of the bus (240 Ω
total, so a healthy powered-down bus measures ~60 Ω between CANH and CANL). The
Microsquirt end is typically already terminated; if the dash is the far end of your
bus, enable/fit the 120 Ω terminator at the Feather (the Feather M4 CAN has
pads/jumper for its onboard termination resistor - check your board revision's
silkscreen). A short bench pigtail will often work unterminated; a car harness won't.

## MegaSquirt / TunerStudio configuration

1. In TunerStudio, open **CAN-bus/Testmodes** (MS2/Extra).
2. Enable **Simplified Dash Broadcasting**.
3. Note the **CAN ID (Base)** - default **1512** (0x5E8). It must match
   `BASE_CAN_ID` in `config.py`.
4. Set the CAN baud rate to **500 kbps** (the MS2/Extra default) to match
   `CAN_BAUD_RATE`.
5. Burn, power-cycle the ECU, and the dash should light up within a second.

## CircuitPython setup

1. Install **CircuitPython 10.x** on the Feather M4 CAN
   ([downloads page](https://circuitpython.org/board/feather_m4_can/)).
   Developed and tested against **CircuitPython 10.2.1**.
2. Download the matching **Adafruit CircuitPython Library Bundle** (10.x series)
   from the [bundle releases page](https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases).
   Tested against bundle **20260710**.
3. Copy these from the bundle into `CIRCUITPY/lib/`:

   | Library | Used for |
   |---|---|
   | `adafruit_display_text/` (folder) | all text (`bitmap_label`) |
   | `adafruit_ili9341.mpy` | TFT driver |
   | `adafruit_tsc2007.mpy` | touch controller |
   | `adafruit_sdcard.mpy` | SD card datalogging |
   | `adafruit_bus_device/` (folder) | dependency of the above |
   | `adafruit_ticks.mpy` | dependency of `adafruit_display_text` (current bundles) |

   Copy **only** these - the M4's ~2 MB internal flash is shared with your code,
   and dumping the whole bundle in will fill it. (`canio`, `displayio`,
   `bitmaptools`, `terminalio`, `struct`, `storage`, `supervisor` are built into
   CircuitPython itself.)

## Install

Copy these repo files to the **root** of the `CIRCUITPY` drive:

```
code.py  config.py  canbus.py  ui.py  touch.py  datalog.py  ticks.py  boot.py
```

CircuitPython auto-runs `code.py` on power-up. Optionally add a `splash.bmp`
(see [Custom splash screen](#custom-splash-screen)).

## Usage

- **Navigate:** tap the **left half** of the screen for the previous page, the
  **right half** for the next. Pages wrap around; the dots along the bottom show
  where you are. (Taps, not swipes - resistive panels are too clunky for reliable
  drag gestures.)
- **Page order:** RPM → MAP → Boost → Coolant → TPS → AFR → Battery → MAT →
  Overview grid → Boost/MAT beam page.
- **Peak line:** single-gauge pages show `PEAK ###` (highest since power-on).
  AFR shows `LO ##.# HI ##.#` (richest and leanest); Battery shows `AVG ##.#`.
  All reset only on power cycle.
- **Stale data:** a channel with no CAN frame for 1 s shows `---` in red plus a
  `NO CAN` banner (top-right). Normal again the instant frames return.
- **Datalogging:** top-left corner always shows `NO SD` / `LOG OFF` / `LOG ON`.
  With a card inserted, a session starts automatically when RPM exceeds
  `ENGINE_RUNNING_RPM` (default 300) and ends when the engine stops. Files land
  in `/sd/logs/log001.csv`, `log002.csv`, ... - plain CSV with a name row and a
  units row, which **MegaLogViewer HD** opens directly (deliberately not the
  proprietary binary `.mlg` format, which can't be safely written blind).

## Configuration

Everything tunable lives in **`config.py`**, each entry commented with units,
valid range, and where to find the right value for your setup. Highlights:

- `BASE_CAN_ID` - must match TunerStudio's CAN ID (Base)
- `ATMOSPHERIC_KPA_OVERRIDE` - Boost's barometric reference. Default `None` =
  **self-calibrating**: engine-off MAP *is* local baro, so the dash locks the
  reference from the first RPM==0 frames, keeps tracking it whenever the
  engine is off (a mountain fuel stop recalibrates it), and freezes it while
  running - correct at any altitude with zero setup. Set a number (kPa) to
  pin it instead; your correct pinned value is simply the engine-off MAP
  reading
- `RPM_RED_AT`, `SHIFT_BAR_MAX_RPM` - match your redline
- `*_STOPS` threshold tables - every gauge's color bands
- `TS_RAW_X_MIN` / `TS_RAW_X_MAX` - touch calibration (swap them if left/right
  taps are reversed)
- `DISPLAY_ROTATION` - `90` (default) or `270` if your screen is upside-down
- `ENGINE_RUNNING_RPM`, `LOG_INTERVAL_MS` - datalog gating and rate
- `DEBUG` - print loop rate / worst loop time / free memory over USB serial

## Custom splash screen

`code.py` shows `/splash.bmp` for 2 s at boot if present (silently skipped
otherwise). Requirements: exactly **240x320**, **16-bit RGB565 BMP** (displayio's
`OnDiskBitmap` doesn't reliably read 24/32-bit BMPs). Quick conversion on Windows
PowerShell:

```powershell
Add-Type -AssemblyName System.Drawing
$src = [System.Drawing.Image]::FromFile("your-image.png")
$dest = New-Object System.Drawing.Bitmap 240, 320, ([System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$g = [System.Drawing.Graphics]::FromImage($dest)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.DrawImage($src, 0, 0, 240, 320)
$dest.Save("splash.bmp", [System.Drawing.Imaging.ImageFormat]::Bmp)
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Every page shows `---` / `NO CAN` | Broadcast not enabled in TunerStudio; `BASE_CAN_ID` mismatch; baud mismatch (both ends 500 kbps); CANH/CANL swapped; missing 120 Ω termination; transceiver standby pin not released (the code drives `CAN_STANDBY` low automatically - if you're on a non-Feather-M4-CAN board, check yours) |
| A value is off by exactly **10x** | That channel's scale assumption doesn't match your firmware's broadcast. All fields here are `raw / 10` except RPM (`raw / 1`) per the official MS2 broadcast doc - verify with a candump and adjust the decode in `canbus.py` |
| MAT or Coolant reads ~6500 when it should be cold | You're on very old firmware that packs those fields unsigned at a different offset - check `OFS_MAT` / byte offsets in `config.py` against your INI |
| Screen stays white / blank | FeatherWing not fully seated; wrong FeatherWing version (needs **V2**); try lowering `TFT_BAUDRATE` |
| Screen upside-down | Set `DISPLAY_ROTATION = 270` |
| Left/right taps reversed | Swap `TS_RAW_X_MIN` and `TS_RAW_X_MAX` |
| Touch does nothing | V1 FeatherWing (STMPE610) - this code needs the V2's TSC2007; check the serial console for the probe message |
| `NO SD` with a card inserted | Card not FAT32; card not fully clicked in; some very large (>32 GB) cards ship exFAT - reformat FAT32 |
| `ValueError: incompatible .mpy file` on boot | Library bundle doesn't match your CircuitPython major version - use the 10.x bundle with CircuitPython 10 |
| Boost slightly off after the dash rebooted while driving | The baro reference couldn't self-capture (it needs to see RPM = 0) so it's running on the sea-level fallback; it locks correctly the next time the engine is off. Pin `ATMOSPHERIC_KPA_OVERRIDE` if your install power-cycles the dash mid-drive routinely |
| Values freeze but no `NO CAN` | Shouldn't happen (stale timeout is per-channel) - check the serial console for tracebacks and please open an issue |

## Repo structure

```
code.py         thin main loop (construct subsystems, cycle updates)
config.py       ALL tunables: CAN IDs/offsets, thresholds, colors, layout, timing
canbus.py       canio setup w/ hardware ID filters, frame decode, EcuData store
ui.py           displayio UI: pages, bars, markers, banners, render gating
touch.py        TSC2007 polling + tap-zone state machine
datalog.py      SD mount + engine-gated CSV session logger
ticks.py        rollover-safe millisecond timing helpers
boot.py         intentionally empty (see its comments for why)
PERFORMANCE.md  architecture + optimization rationale
```

(The original pre-refactor single-file version is preserved in git history at
the `pre-refactor` tag.)

## License

MIT - see [LICENSE](LICENSE).
