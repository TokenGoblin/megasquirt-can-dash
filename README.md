# MegaSquirt CAN Digital Dash

**A full-featured digital dash for MegaSquirt/Microsquirt ECUs, built from two
off-the-shelf Adafruit boards and a few readable Python files — for a fraction of
the cost of a commercial CAN dash.**

[![CircuitPython 10.x](https://img.shields.io/badge/CircuitPython-10.x-7d5bed)](https://circuitpython.org/board/feather_m4_can/)
[![Board: Feather M4 CAN](https://img.shields.io/badge/board-Feather%20M4%20CAN-00a2a2)](https://www.adafruit.com/product/4759)
[![ECU: MegaSquirt MS2/Extra](https://img.shields.io/badge/ECU-MegaSquirt%20MS2%2FExtra-c8102e)](https://www.msextra.com/)
[![Tests](https://github.com/TokenGoblin/megasquirt-can-dash/actions/workflows/tests.yml/badge.svg)](https://github.com/TokenGoblin/megasquirt-can-dash/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A CircuitPython digital dash for a MegaSquirt Microsquirt (MS2/Extra) ECU, built on an
Adafruit Feather M4 CAN Express with a 2.4" TFT touchscreen FeatherWing. Ten pages of
color-coded gauges, a race-style RPM shift-light bar, continuous beam bars with
peak-hold, a Boost/MAT beam-gauge page, and CSV datalogging to a microSD card.

<!-- PHOTOS GO HERE - the highest-impact addition to this README:
       1. The dash lit up in the car   <- this one matters most
       2. The RPM shift-light bar mid-sweep
       3. The 2x2 Overview page
     Put them in docs/images/ and link as:  ![Dash in car](docs/images/dash-in-car.jpg)
     Then set Settings -> Social preview to shot #1: that is the card people see
     when this link is posted to a forum, Reddit, or Discord. -->

> **New build?** Read [Hardware](#hardware) and
> [Wiring and CAN termination](#wiring-and-can-termination) first, then work
> through [BRINGUP.md](BRINGUP.md) on the bench before the car. Two of the bugs
> in this project's history were caught by that checklist and could not have been
> found any other way.

## Contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [Hardware](#hardware)
- [Wiring and CAN termination](#wiring-and-can-termination)
- [MegaSquirt / TunerStudio configuration](#megasquirt--tunerstudio-configuration)
- [CircuitPython setup](#circuitpython-setup)
- [Install](#install)
- [Usage](#usage)
- [Configuration](#configuration)
- [Fault handling](#fault-handling)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Tests](#tests)
- [Repo structure](#repo-structure)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

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
- **Cross-page alarms** - every channel is checked on every render tick, not
  just the one on screen, so an overheat on page 4 is visible from page 1: a
  blinking banner names the channel and its value (`COOLANT 238`) and the
  reading itself gains a `!`. Deliberately readable without color, for the
  ~8% of men with red/green color deficiency and for anyone in direct sun
- **Implausible readings are dropped, not shown** - a corrupt frame can't
  leave `PEAK 6553.5` stuck on a page for the rest of the drive
- **Stale-data safety** - any channel silent for >1 s shows `---` and a `NO CAN`
  banner (also raised instantly if the CAN controller reports a failed bus state)
- **Engine-gated CSV datalogging** to microSD, MegaLogViewer HD compatible
- **Tap navigation** - left half = previous page, right half = next page
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

### The dash is an active bus participant

Worth understanding before you wire this into a car. Although the dash only ever
*reads* MegaSquirt data and never sends a frame of its own, it is not electrically
passive: like any normal CAN node it acknowledges the frames it receives, and it
signals an error when it sees one it can't decode.

That matters if the dash is **misconfigured**. A baud rate that doesn't match the
ECU doesn't just leave you looking at `---`; the dash will sit there signalling
errors onto the bus, and because the controller is set to recover automatically
from bus-off it will keep doing so. On the two-node bus described above - one
MegaSquirt, one dash - the practical result is just a blank dash. On a bus that
carries traffic your engine depends on (an MS3 expansion board, a CAN wideband, a
CAN-connected ignition or transmission controller) it can disturb those nodes too.

So: **double-check `CAN_BAUD_RATE` matches your ECU** before the first drive.
`config.py` also has a `CAN_SILENT_MODE` switch that makes the dash incapable of
transmitting anything at all - but read its comment first, because it is *not*
automatically the safer setting. On a plain ECU-plus-dash bus the dash is the
ECU's only source of acknowledges, and going silent there causes the problem it
was meant to avoid. Consider it only if other nodes are present to acknowledge.

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

   Copy **only** these - the M4's ~2 MB internal flash is shared with your code,
   and dumping the whole bundle in will fill it. (`canio`, `displayio`,
   `bitmaptools`, `terminalio`, `struct`, `storage`, `supervisor` are built into
   CircuitPython itself.)

## Install

Copy these repo files to the **root** of the `CIRCUITPY` drive:

```
code.py  config.py  canbus.py  ui.py  touch.py  datalog.py  ticks.py  boot.py
```

CircuitPython auto-runs `code.py` on power-up. Nothing else is needed - the
dash comes up blank within about a second and starts drawing as soon as the
first CAN frames arrive.

## Usage

- **Navigate:** tap the **left half** of the screen for the previous page, the
  **right half** for the next. Pages wrap around; the dots along the bottom show
  where you are. (Taps, not swipes - resistive panels are too clunky for reliable
  drag gestures.)
- **Reset the peaks:** press and **hold** anywhere for ~1.5 s. Every `PEAK` /
  `LO` / `HI` / `AVG` readout clears; the readouts snapping to `---` is the
  confirmation. Because taps act on the press edge, the hold's own press
  changes the page first - it is put back when the hold registers. Without
  this the only way to clear a peak was a power cycle, which also ends your
  datalog session.
- **Alarms:** if any channel crosses its limit in `config.py`'s `ALARMS` table
  while the engine is running, a blinking banner naming the channel and value
  appears **on every page**, and that channel's reading gains a trailing `!`.
  Limits are wired to the same constants as the color bands, so a gauge's
  "red" and its alarm can't drift apart. RPM is deliberately not alarmed -
  see the comment in `config.py`.
- **Page order:** RPM → MAP → Boost → Coolant → TPS → AFR → Battery → MAT →
  Overview grid → Boost/MAT beam page.
- **Baro reference:** the Boost page shows the barometric reference its zero
  point is derived from - `BARO 84.2` once measured from engine-off MAP, or
  `BARO 101.3 EST` in orange while the dash is still running on the sea-level
  fallback (it powered up mid-drive, or hasn't yet seen enough engine-off
  frames to agree on a lock). `EST` means boost is offset by however far your
  local pressure is from sea level.
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
- `BARO_LOCK_SAMPLES`, `BARO_LOCK_SPREAD_KPA` - how many consecutive
  engine-off frames must agree before that reference first locks, so one
  corrupt frame from a booting ECU can't set a wrong boost zero for the whole
  drive. The Boost page shows the reference it is using, and marks it `EST`
  while it's still the sea-level fallback rather than a measurement
- `RPM_RED_AT`, `SHIFT_BAR_MAX_RPM` - match your redline
- `*_STOPS` threshold tables - every gauge's color bands
- `ALARMS`, `ALARM_PRIORITY`, `ALARM_ONLY_WHEN_RUNNING` - which channels raise
  a cross-page alarm, and which wins when several do at once
- `SANE_RANGES` - per-channel plausibility bounds; a decoded value outside its
  range is dropped rather than displayed. Widen if your setup legitimately
  reads outside them (the defaults are deliberately generous)
- `CAN_SILENT_MODE` - make the dash incapable of transmitting. Read its
  comment and [the wiring note](#the-dash-is-an-active-bus-participant) before
  enabling: it is not automatically the safer choice
- `TOUCH_HOLD_MS` - how long a hold must last to reset the peak readouts
- `TS_RAW_X_MIN` / `TS_RAW_X_MAX` - touch calibration (swap them if left/right
  taps are reversed)
- `DISPLAY_ROTATION` - `90` (default) or `270` if your screen is upside-down
- `ENGINE_RUNNING_RPM`, `LOG_INTERVAL_MS` - datalog gating and rate
- `MAX_FAULTS_BEFORE_HALT`, `FAULT_CLEAR_MS` - how many caught errors the main
  loop tolerates before giving up (see [Fault handling](#fault-handling))
- `WATCHDOG_TIMEOUT_S` - hardware watchdog, **off by default**; set ~4.0 for a
  dash that lives in a car, leave 0 while developing on the bench
- `DEBUG` - print loop rate / worst loop time / free memory over USB serial

## Fault handling

The main loop catches every exception instead of letting one stop the dash.
The reasoning: every subsystem here is optional **except the display**, and a
dash that dies at 70 mph doesn't come back without a power cycle. So a yanked
SD card, a full card, or a touch chip that glitches on a bump costs you that
subsystem and nothing else - logging switches itself off (`NO SD`) and the
gauges keep running.

When a fault is caught, a small red `FLT n` counter appears at the top center
of the screen and clears itself after `FAULT_CLEAR_MS` of quiet. A single
`FLT 1` that clears is worth noting but not worth stopping for; a counter that
climbs means something is failing continuously, and at
`MAX_FAULTS_BEFORE_HALT` the dash stops on a red `LOOP FAULT` screen rather
than leave you guessing whether the numbers are still live. Every fault also
prints to the USB serial console with its exception - that's where to look.

Setting `WATCHDOG_TIMEOUT_S` covers the other failure shape: a *hang* rather
than an exception. The loop feeds the watchdog once per pass, so if it ever
stops making progress the board resets itself and the dash comes back on its
own. It is off by default because not every CircuitPython port supports it
(the code reports that over serial and carries on), and because in RESET mode
the timer keeps running while you sit at the REPL, which reboots the board
every few seconds while you're developing.


## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Every page shows `---` / `NO CAN` | Broadcast not enabled in TunerStudio; `BASE_CAN_ID` mismatch; baud mismatch (both ends 500 kbps); CANH/CANL swapped; missing 120 Ω termination; transceiver standby pin not released (the code drives `CAN_STANDBY` low automatically - if you're on a non-Feather-M4-CAN board, check yours) |
| A value is off by exactly **10x** | That channel's scale assumption doesn't match your firmware's broadcast. All fields here are `raw / 10` except RPM (`raw / 1`) per the official MS2 broadcast doc - verify with a candump and adjust the decode in `canbus.py` |
| MAT or Coolant reads ~6500 when it should be cold | You're on very old firmware that packs those fields unsigned at a different offset - check `OFS_MAT` / byte offsets in `config.py` against your INI |
| Screen stays white / blank | FeatherWing not fully seated; wrong FeatherWing version (needs **V2**); try lowering `TFT_BAUDRATE` |
| Screen upside-down | Set `DISPLAY_ROTATION = 270` |
| Left/right taps reversed | Swap `TS_RAW_X_MIN` and `TS_RAW_X_MAX` |
| Touch does nothing, `NO TOUCH` at the top-left | The touch controller wasn't found at boot: V1 FeatherWing (STMPE610 - this code needs the V2's TSC2007), or a bad solder joint on the I2C lines. The dash runs fine without it, just fixed on one page |
| Taps need a very firm press | Lower `TOUCH_PRESSURE_THRESHOLD`. A contact that stays below it for ~100 ms is written off until it lifts, so something resting on the panel can't hold navigation hostage |
| A channel shows `---` but the bus is fine | Its value is outside `SANE_RANGES` and is being dropped as implausible - deliberately, so a corrupt frame can't be mistaken for a reading. The serial console shows nothing for this; check the raw value with a candump and widen the range if your setup genuinely reads there |
| A banner names a channel that seems fine | An `ALARMS` limit is set tighter than your engine actually runs. The alarm only fires with the engine running above `ENGINE_RUNNING_RPM` |
| `NO SD` with a card inserted | Card not FAT32; card not fully clicked in; some very large (>32 GB) cards ship exFAT - reformat FAT32 |
| `ValueError: incompatible .mpy file` on boot | Library bundle doesn't match your CircuitPython major version - use the 10.x bundle with CircuitPython 10 |
| Boost slightly off after the dash rebooted while driving | The baro reference couldn't self-capture (it needs to see RPM = 0) so it's running on the sea-level fallback; it locks correctly the next time the engine is off. Pin `ATMOSPHERIC_KPA_OVERRIDE` if your install power-cycles the dash mid-drive routinely |
| Values freeze but no `NO CAN` | Shouldn't happen (stale timeout is per-channel) - check the serial console for tracebacks and please open an issue |
| Red `FLT n` at the top of the screen | The main loop caught and survived an error - see [Fault handling](#fault-handling). Most often a flaky microSD card (check `NO SD` in the top-left); the serial console names the exception |
| Red `LOOP FAULT` screen | Something failed continuously, not once. The dash halted deliberately rather than show numbers it can't vouch for. Read the serial console, then power-cycle |

## Known limitations

Stated plainly, so you can judge the risk before wiring this into a car. Most of
these are *not yet verified* rather than *known broken* — [BRINGUP.md](BRINGUP.md)
is the checklist that closes them out, and [AUDIT-REPORT.md](AUDIT-REPORT.md)
records the exact evidence that would settle each one.

- **It has not been driven yet.** The bench run on 2026-08-11 covered boot,
  memory/loop timing, and fault containment on real hardware. Display legibility,
  touch, SD logging, live CAN decode, alarms, and the in-car soak are all still
  deferred — no card, no live ECU, no eyes on the panel in daylight.
- **The CAN decode is validated against one firmware build.** Every byte offset,
  sign, and scale in `config.py` traces to the EFI Analytics "MegaSquirt CAN
  realtime data broadcast protocol" note plus the author's own Microsquirt. There
  is no committed `candump` fixture, so a build broadcasting a different layout
  would decode wrong — a value off by exactly 10x is almost always this. See
  [Troubleshooting](#troubleshooting).
- **MS3 and other variants are untested.** They should work if they broadcast
  Simplified Dash, but nobody has confirmed it. Even "worked unchanged" is a
  useful report.
- **RAM is tight** — about 16.5 KB free after startup on CircuitPython 10.2.1.
  Two hardware-found bugs were both `MemoryError`s, one of them in the error
  screen itself. Adding pages or full-screen bitmaps is not free; check the
  `DEBUG = True` memory line before and after any UI change.
- **The 2.4" FeatherWing must be V2.** V1 uses an STMPE610 touch controller this
  code doesn't speak. The dash still runs without touch, just fixed on one page.
- **Sunlight legibility is unverified.** The dim greys used for the page arrows
  and inactive page dots were reviewed on a monitor, not on a panel in daylight.
- **Datalogging flushes every row**, which briefly stalls the loop while a
  session is active. Deliberate: a key-off should never cost more than one row.
- **Peak / LO / HI / AVG readouts survive stale data** and clear only on a
  touch-hold or a power cycle.

## Tests

The pure logic - fixed-point formatting, tick rollover, CAN decode, the
barometric lock, boost conversion, bar geometry, log-file numbering, config
validation - runs on desktop Python with no hardware and no dependencies:

```
python tests/run_tests.py        # -v for per-test names
```

`tests/stubs.py` stands in for the CircuitPython-only modules (`board`,
`canio`, `displayio`, ...); everything under test is the real dash code.

Either the repo root or `tests/` works as a working directory. The suite ships
its own runner rather than a bare `python -m unittest` for a specific reason:
CircuitPython requires the main file be named `code.py`, which shadows Python's
standard-library `code` module for any tool that imports it (`pdb` does, so most
linters and test runners do). `run_tests.py` fixes up the path before importing
anything, which is what makes both invocations safe.

Every push runs this suite on Linux against Python 3.11/3.12/3.13
([workflow](.github/workflows/tests.yml)).

## Repo structure

Everything that goes on the device is in the repo root — the eight files listed
under [Install](#install), and nothing else:

```
code.py                  thin main loop (construct subsystems, cycle updates)
config.py                ALL tunables: CAN IDs/offsets, thresholds, colors, layout, timing
canbus.py                canio setup w/ hardware ID filters, frame decode, EcuData store
ui.py                    displayio UI: pages, bars, markers, banners, render gating
touch.py                 TSC2007 polling + tap-zone state machine
datalog.py               SD mount + engine-gated CSV session logger
ticks.py                 rollover-safe millisecond timing helpers
boot.py                  intentionally empty (see its comments for why)
```

Everything else stays on your computer:

```
tests/                   desktop test suite (no hardware needed) + CircuitPython stubs
.github/workflows/       CI: runs the suite on Python 3.11/3.12/3.13 every push
.github/ISSUE_TEMPLATE/  bug report + MegaSquirt variant report forms
README.md                this file
CONTRIBUTING.md          development setup and conventions
PERFORMANCE.md           architecture + optimization rationale
BRINGUP.md               hardware bring-up checklist + bench results
AUDIT-REPORT.md          full code audit: findings, and what stayed unverified
THIRD-PARTY-NOTICES.md   dependency + licensing inventory
```

(The original pre-refactor single-file version is preserved in git history at
the `pre-refactor` tag.)

## Documentation

Four companion documents. None are required reading to *use* the dash:

| Document | What it covers |
|---|---|
| [BRINGUP.md](BRINGUP.md) | Stage-by-stage hardware bring-up, from "before you plug anything in" through the in-car soak — plus the bench-run results and the two bugs they caught. **Start here for a new build.** |
| [PERFORMANCE.md](PERFORMANCE.md) | Architecture and optimization rationale: the cooperative main loop, the fixed-point data model, GC discipline, measured loop rates. Read before changing the render path. |
| [AUDIT-REPORT.md](AUDIT-REPORT.md) | Full code audit — 18 findings, what was verified, and (more usefully) what could *not* be verified, each paired with the evidence that would settle it. |
| [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) | Dependency and licensing inventory, including an AI-generated-code disclosure. |

## Contributing

Forks and adaptations are the point — see [Why this exists](#why-this-exists).
Issues and pull requests are both welcome, and "I built one and it worked" is a
genuinely useful report for a project with one known install.

Most valuable contributions right now, roughly in order:

1. **A `candump` capture** of IDs 1512–1515 from a running engine, alongside the
   same values as TunerStudio displays them. This is the single biggest open gap
   in the project: it would turn the decode table from *validated on one car*
   into a committed test fixture. See [Known limitations](#known-limitations).
2. **Confirmation on other MegaSquirt variants** — MS3, other firmware builds,
   non-default broadcast base IDs.
3. **Photos of the panel in daylight**, the only way to settle the legibility
   questions the audit left open.
4. **Bug reports from a moving car**, especially anything involving the `FLT n`
   counter or the barometric lock.

Logic changes should come with a test — the suite runs on desktop Python with no
hardware and no dependencies (see [Tests](#tests)). Please keep `config.py` as
the single home for tunables; that separation is what makes this forkable.

[CONTRIBUTING.md](CONTRIBUTING.md) has the practical detail: development setup,
what the tests can and can't catch, and the conventions worth knowing before
touching the main loop or the render path.

## License

MIT - see [LICENSE](LICENSE).
