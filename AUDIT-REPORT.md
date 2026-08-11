# AUDIT-REPORT.md

Audit of `megasquirt-can-dash` @ `e06fc43` (branch `main`, clean tree).
Performed 2026-08-11 against `AUDIT.md` §0–§19.

> **Remediation status (updated 2026-08-11, after the audit was delivered).**
> **All three tiers of the plan have been implemented in the working tree.**
> Every finding below is now either fixed or explicitly accepted; the table
> says which.
>
> | Finding | Status |
> |---|---|
> | F-01 loop fault containment | Fixed — `try/except` + `FLT` counter + `LOOP FAULT` halt + optional watchdog (off by default) |
> | F-02 unguarded SD writes | Fixed — write/flush/close/start all degrade to `NO SD` |
> | F-03 single-frame baro lock | Fixed — N-frame consensus lock on the mean; `BARO … EST` shown while unlocked |
> | F-04 `os.listdir` per session | Fixed — boot-time bisect probe, ~20 stats for 1,000 files, 1 per session |
> | F-10 splash `ValueError` | Fixed — handler widened to `Exception` |
> | F-11 no config validation | Fixed — `config.validate()` at startup, reports by setting name on the fatal screen |
> | F-12 no tests | Fixed — 100 tests in `tests/`, desktop-runnable, no hardware |
> | F-13 shift-light off-by-one | Fixed — bulbs coloured for the RPM at which they light |
> | F-15 unused import | Fixed |
> | F-05 no cross-page alerting | Fixed — every channel scanned each tick; banner names channel + value on all 10 pages |
> | F-06 colour-only alarms | Fixed — banner text + `!` on the value + blink; alarm state legible with no colour at all |
> | F-07 no plausibility gate | Fixed — `SANE_RANGES` drops (never clamps) impossible samples in `EcuData.set`; hold-to-reset for peaks |
> | F-08 active bus participation | Fixed as documentation — README section + `CAN_SILENT_MODE`; **the code default is unchanged on purpose** (see below) |
> | F-09 unbounded accumulators | Fixed — `low`/`avg` kept only for the channels that display them |
> | F-14 bare numbers | Fixed — units on Overview and Beam pages |
> | F-16 sub-threshold press churn | Fixed — bounded retry, then written off until lift |
> | F-17 banner coverage | Fixed — falls out of F-05 |
> | F-18 silent touch failure | Fixed — `NO TOUCH` on the panel |
>
> **Nothing is left open, but two things were deliberately not "fixed" the
> obvious way**, and both deserve the author's eye:
>
> - **F-08.** `CAN_SILENT_MODE` defaults to `False` — i.e. the dash still ACKs
>   and can still signal errors. Flipping it would be wrong: on the two-node
>   bus this project describes, the dash is the ECU's only source of
>   acknowledges. The real deliverable was the README section explaining that a
>   baud mismatch disturbs the bus rather than merely blanking the display.
> - **F-07's reset gesture.** Taps fire on the press edge by design, so a hold
>   cannot be distinguished before its own press has already changed the page.
>   The hold undoes that step when it registers. It works, but it is a
>   compromise with an existing design decision rather than a clean solution.
>
> The findings below are unedited: they describe the code as audited, not as it
> now stands. The `UNVERIFIED` list stands apart from item 5 (F-10's exception
> class no longer matters — the handler catches both), and **item 1 remains the
> most valuable thing anyone could do for this project**: a candump fixture
> would let the suite verify the frame layout itself, which is the one piece of
> correctness 154 tests still take on faith. Item 6 also stands and has grown —
> the alarm banner, the blink, the `!` suffix, the unit labels and the
> `NO TOUCH` indicator are all verified by arithmetic (widths, overlaps,
> thresholds) but **none of them has been seen on a panel**.

**Appendices activated:** Appendix A (safety-critical / hardware / embedded) — the
software reads sensor data a driver acts on and shares an electrical bus with a
running engine's ECU.
**Considered and ruled out:** B (no network service), C (not a library others
depend on — it is a firmware payload), D (no pipeline), E (no ML), F (not an
installed desktop/mobile app — no permissions, update channel, or OS lifecycle),
G (no infrastructure, no CI at all).

---

## Executive summary

Three things matter most.

**1. There is no fault containment.** `code.py`'s `while True:` loop has no
`try/except` and no watchdog, and the SD write path inside it is unguarded. One
`OSError` from a jostled microSD card — or the `MemoryError` that becomes
inevitable after a few hundred log sessions (F-04) — stops the dash permanently,
mid-drive, until a power cycle. For an instrument you look at while driving, this
is the single most consequential gap. Every other finding is smaller than this one.

**2. The self-calibrating boost zero can latch onto one bad frame.** The
barometric reference locks from the *first* CAN frame with RPM == 0 inside a
55–110 kPa window, then freezes for the entire drive once the engine starts. One
garbage-but-plausible frame during ECU boot, followed by an immediate crank, puts
a fixed offset on every boost reading and every logged boost row, with no on-screen
indication that anything is wrong. I measured a 6.0 PSI error from a single 60 kPa
frame.

**3. As a monitoring instrument it can only warn you about the page you are
looking at.** Ten pages, one visible; a coolant temperature climbing into the red
on page 4 is completely invisible from page 1. Alert state is also carried by
colour alone, which fails for red/green colour-blind users.

**Verdict:** the engineering is unusually careful — the fixed-point data model,
change-gated rendering, hardware CAN filters, and rollover-safe timing are all
correct and genuinely well-reasoned, and the documentation is honest about its own
trade-offs. It is fit for its stated purpose as a hobbyist advisory dash. It is
not yet fit to be *relied on* mid-drive, because it has no answer for its own
failure. Fix F-01/F-02/F-04 and that changes.

No Criticals. Nothing here corrupts data irrecoverably, moves money, or commands
hardware. No secrets, no vulnerable dependencies, no licensing problems.

---

## Inferred project context

The intake block in `AUDIT.md` was left empty. **The following is inference from
the README, source, and git history — not stated by the author.** Corrections here
change the severity calibration of everything below.

```yaml
project:            CircuitPython digital dash for a MegaSquirt Microsquirt
                    (MS2/Extra) ECU on an Adafruit Feather M4 CAN Express with a
                    2.4" TFT FeatherWing. Decodes 4 broadcast CAN frames, renders
                    10 gauge pages, logs CSV to microSD.
stakes:             INFERRED — advisory instrument, not a controller. It never
                    commands the engine; the ECU does. Realistic harm: (a) a
                    driver or tuner acts on a wrong reading (over-boost, missed
                    overheat) and damages an engine — thousands of dollars, not
                    injury; (b) the dash dies mid-drive and instrumentation is
                    lost; (c) it shares an electrical bus with a running engine's
                    ECU and can disturb it if misconfigured. NOT: money, PII,
                    multi-user data, network exposure.
users:             INFERRED — hobbyist MegaSquirt owners, technical enough to
                    solder and edit config.py, explicitly invited to fork. Read
                    the display while driving. Under pressure only in the sense
                    that a car is moving.
maturity:          INFERRED — shipping/personal, pre-release for others. Validated
                    on the author's own vehicle (PERFORMANCE.md cites live
                    measurements); no evidence of any second installation.
irreversible_ops:  INFERRED — creating/appending CSV files on the user's microSD
                    (never deletes, never overwrites); consuming card space;
                    electrically driving CANH/CANL (ACK and error frames) on the
                    vehicle bus. No firmware writes, no destructive commands.
trust_boundaries:  INFERRED — (1) CAN frames from the ECU/bus, unauthenticated by
                    protocol design; (2) /splash.bmp on the CIRCUITPY drive;
                    (3) config.py, edited by the user; (4) existing filenames on
                    the microSD card. Author flagged none of these explicitly.
compliance:        INFERRED — none. No personal data, no regulated function.
out_of_scope:      backup/ (gitignored, local-only, never edited per .gitignore);
                    Adafruit libraries (not vendored, user-supplied).
prior_known_issues: README "Known limitations" is referenced but the section does
                    not exist in README.md; the Troubleshooting table documents
                    accepted rough edges (mid-drive power-cycle baro fallback,
                    10x scale mismatches on other firmware, V1 FeatherWing).
```

**Calibration applied:** advisory-instrument stakes. I have not manufactured
Criticals. Appendix A's escalation is applied to the two places it genuinely bites
— the bus-transmit path (F-08) and the derived-value correctness path (F-03) —
and *not* to the rest, because nothing else here touches hardware that can be
damaged.

---

## Repository map

**Shape.** Pure CircuitPython 10.x (targets 10.2.1) for a SAME51 / Feather M4 CAN.
2,497 lines total, no build system, no package manifest, no lockfile, no CI, no
tests, no vendored code.

| File | Lines | Role |
|---|---|---|
| `ui.py` | 941 | All displayio rendering: pages, bars, markers, banners, change-gating |
| `config.py` | 412 | Every tunable. Data only, plus derived stop tables |
| `canbus.py` | 310 | canio setup, hardware ID filters, frame decode, `EcuData` store |
| `README.md` | 213 | Install, wiring, config, troubleshooting |
| `datalog.py` | 182 | SD mount, engine-gated CSV session logger |
| `PERFORMANCE.md` | 151 | Architecture and optimization rationale |
| `touch.py` | 117 | TSC2007 polling, tap-zone state machine |
| `code.py` | 99 | Main loop |
| `ticks.py` | 50 | Rollover-safe ms timing |
| `boot.py` | 22 | Intentionally empty; documents why |

**Entry points.** Exactly one: CircuitPython auto-runs `code.py` at power-up.
`boot.py` runs before it and does nothing. There is no CLI, no HTTP, no callback
registered with any driver, no exported API. Every other module is imported and
driven synchronously from the one `while True:` loop. This is a genuinely small
attack and failure surface and it is worth saying so plainly.

**Trust boundaries.**

| Boundary | Entry point | Validated? |
|---|---|---|
| CAN frames from the bus | `CanBus.update` → `_decode` (`canbus.py:220-310`) | Structurally yes (hardware ID filter + `len(data) < 8` guard + fixed-width `struct.unpack`). Semantically **no** — see F-07 |
| `/splash.bmp` on CIRCUITPY | `DashUI._show_splash` (`ui.py:746-757`) | Partially — `OSError` caught, `ValueError` not (F-10) |
| `config.py` (user-edited) | Import time, everywhere | **No** validation at all (F-11) |
| Filenames on the microSD | `DataLogger._next_log_path` (`datalog.py:89-106`) | Parse errors handled; unbounded count is not (F-04) |

**The irreversible set.** Small and benign, which is the good news of this audit:

1. `open(path, "w")` + `write` + `flush` under `/sd/logs/` (`datalog.py:115-123`,
   `181-182`). Creates new numbered files only; never opens an existing name for
   write, never deletes, never truncates. Traced every route: the *only* caller of
   `_start()` is the engine gate in `update()` — one chokepoint, no back door, no
   debug flag, no test hook. Consumes card space without bound (see Clean bill note).
2. Driving CANH/CANL. The controller is opened in normal (non-silent) mode, so it
   ACKs and can emit error frames onto a bus shared with a running engine's ECU
   (F-08). The dash never transmits a data frame — verified: there is no
   `send()` call anywhere in the repo.
3. `storage.mount(vfs, "/sd")` — reversible, and deliberately *not* remounting
   internal flash (`boot.py` documents why at length, correctly).

**Data model.** `EcuData` (`canbus.py:94-146`): six preallocated parallel lists
indexed by `PARAM_*`, all values integers at 10× real units, matching the wire
format. Source of truth for display *and* logging. No persistence, no schema, no
migrations — everything resets at power-off, by design. Wire format is big-endian
per the MegaSquirt broadcast; read-only (nothing is ever written back to the bus),
so the read/write endianness symmetry check in §6 does not apply.

**Config and secrets.** One flat module, imported directly. No layering, no env
vars, no secrets of any kind — none expected, none found. Missing-config defaults
do not exist as a concept: every value is a literal in `config.py`, so the
"permissive default when config is absent" failure mode cannot occur here.

**Build, CI, deploy.** None, and none needed: "deploy" is copying eight `.py`
files to a USB drive. I verified the README's install list against the tree —
all eight files exist and no others are required. There is no rollback story
beyond keeping the old files, which is reasonable at this scale.

**Git signals.** 8 commits, one tag (`pre-refactor`, present as README claims).
Hotspots: `README.md` and `code.py` (4 touches each), `ui.py`/`config.py` (3).
Zero `TODO`/`FIXME`/`HACK`/`XXX`/`WORKAROUND` markers in the whole tree — verified
by grep, not assumed. No reverted fixes. One commit (`5519c0a`, "Fix peak/LO-HI/AVG
line missing until first CAN frame") is a bug fix whose reasoning is preserved in a
comment at `ui.py:131-135` — good practice, and the `_UNSET` sentinel it introduced
is correct.

**Dead and duplicate code.** `backup/pre-refactor/code.py` is a 1,771-line
single-file ancestor of the current modules — a textbook two-implementations
hazard. It is **not** a live duplicate: it is gitignored, untracked (verified with
`git ls-files`), not copied to the device, and `.gitignore` states it is never
edited. No action needed. In the shipped code: one unused import (F-15), and
`EcuData.low_x10`/`avg_sum` are maintained for all 8 channels but read for only
one each (F-09).

**Documentation drift.** Low, and much better than typical. README's repo
structure, install list, config names, and troubleshooting table all match the
code. Two drifts found: the README links to a `#known-limitations` section that
does not exist, and it describes the `NO CAN` banner as universal when two of the
ten pages never show it (F-17).

### Where I expected the bodies, and where they actually were

This is a well-built piece of embedded Python. The hard parts — fixed-point
arithmetic, tick rollover, change-gated rendering, non-blocking CAN drain — are
the parts that are *right*, and I verified them by re-implementing and executing
them rather than reading them (see §5 below). `format_x10` rounds half-away-from-zero
symmetrically across positives, negatives, and zero; `ticks.diff` is correct across
the 2²⁹ wrap; the AFR centre-zero mapping pins stoich to the exact centre pixel.
I went looking for off-by-one errors in the bar geometry and found only one, and it
is cosmetic.

The bodies are buried in the failure paths, which is exactly where the code stops
being careful: nothing anywhere handles the case where the code itself fails. The
happy path is engineered to a standard the error path never sees.

---

## Findings

### [High] F-01 — Contain faults in the main loop; add a watchdog

- **Location:** `code.py:69-99` (the loop), and by extension every `update()` it calls.
- **What's wrong:** The main loop has no `try/except` at any level, and no
  `microcontroller.watchdog` is configured anywhere in the repo. Any exception
  raised by any subsystem propagates out of `code.py`, ending the CircuitPython VM.
  There is no recovery path: CircuitPython does not auto-restart after an unhandled
  exception in `code.py`, so the dash stays dead until the driver power-cycles it.
- **Trigger:** Anything that raises. The realistic ones, in order of likelihood:
  an `OSError` from the unguarded SD write (F-02); the `MemoryError` from log-file
  enumeration (F-04); a `ZeroDivisionError` from a mis-edited `config.py` (F-11);
  a transient I2C `OSError` from the touch controller on a vibrating dash mount —
  note that `TouchNav.__init__` catches exceptions but `TouchNav.update` does not,
  so a chip that answers at boot and glitches at 60 mph takes the whole dash down.
- **Consequence:** The driver loses all instrumentation at an arbitrary moment
  while driving — coolant temperature, boost, AFR, battery — and cannot get it
  back without stopping to cycle power. The README's own troubleshooting table
  says "Values freeze but no `NO CAN` — shouldn't happen"; this is the mechanism
  by which it happens.
- **Evidence:** `code.py:69` `while True:` with no enclosing handler; the loop body
  at `:72-81` calls four `update()` methods, none wrapped. `grep -rn "watchdog"`
  over the repo: no matches. `TouchNav.update` (`touch.py:76-117`) performs I2C I/O
  at `:94` and `:109` with no handler, while `__init__` at `:47-50` has one.
- **Fix:** Wrap the loop body in `try/except Exception`, and on catch: increment a
  fault counter, set a visible on-screen fault indicator, and continue the loop
  (the render path is the thing most worth keeping alive — a dash that has lost
  logging is still a dash). Escalate to `dash.show_fatal()` only after N
  consecutive faults. Separately, arm `microcontroller.watchdog` with a ~2 s
  timeout and feed it once per pass, so a hang or a fault the handler cannot
  survive results in a reset-and-recover rather than a dark screen. Both belong in
  `code.py` only; no module changes needed.
- **Confidence:** Verified for the code path (no handler exists; no watchdog
  exists). **UNVERIFIED:** exactly what the panel displays after the VM exits —
  CircuitPython resets the display's root group to its console on VM teardown, so
  I expect a traceback in small text rather than frozen gauges, but I could not
  test this without the hardware. If the last frame *does* persist, this finding is
  Critical rather than High, because frozen plausible numbers are worse than a
  blank screen. A single power-cycle test on the bench with a deliberate
  `raise` in the loop would settle it.

### [High] F-02 — Guard the SD write path; a card fault must not take down the display

- **Location:** `datalog.py:181-182` (`write` + `flush`), `datalog.py:133-135`
  (`_stop`'s `close`). Contrast with `datalog.py:68-76` and `:115-128`, which are
  correctly guarded.
- **What's wrong:** `DataLogger.update()` writes and flushes to the SD card on
  every log row with no exception handling, and `_stop()` closes with none. The
  constructor and `_start()` both handle failure carefully and degrade to
  `STATE_NO_SD` — that care stops precisely at the steady-state write, which is
  the call that executes 10 times a second for the entire drive and is by far the
  most likely to fail.
- **Trigger:** Card removed while the engine runs; card full; a bad sector; a card
  that browns out on a marginal 3.3 V rail during cranking; connector vibration on
  a car-mounted FeatherWing. Any of these raises `OSError` from `write` or `flush`.
- **Consequence:** Via F-01, the entire dash stops — display included. The
  datalogger is the *least* important subsystem here (the README calls it optional
  and gates it behind a card being present), and it is the one that can kill the
  most important one. A driver loses their coolant gauge because their SD card came
  loose.
- **Evidence:** `datalog.py:181-182`:
  ```python
  self._file.write(",".join(fields) + "\n")
  self._file.flush()   # crash-safety: never lose more than one row
  ```
  No enclosing `try`. The module's own docstring at `:53-55` states the intended
  contract — "a missing card simply disables logging - it can never affect the
  display" — which the constructor honours and this path does not.
- **Fix:** Wrap the write/flush in `try/except OSError` inside `update()`; on
  failure, `self._file = None` and `self._unavailable = True` so the UI drops to
  `NO SD` and the logger stops trying for the rest of the boot — exactly the
  policy `_start()` already implements at `:125-128`. Do the same in `_stop()`.
  This restores the module's stated contract in about six lines and is the single
  highest value-per-line fix in this report.
- **Confidence:** Verified by reading; the absence of a handler is unambiguous.

### [High] F-03 — Lock the barometric reference from more than one frame

- **Location:** `canbus.py:270-280` (capture and low-pass), `canbus.py:286-291`
  (boost derivation), `config.py:92-109` (tunables).
- **What's wrong:** The baro reference is set by the **first single frame** that
  has RPM == 0 and MAP within 55–110 kPa — `if b is None: self._baro_x10 = map_x10`.
  There is no averaging, no consensus across frames, and no settling delay. Once
  the engine starts, the reference freezes for the rest of the run. The
  plausibility window is 55 kPa wide, so any corrupt-but-in-window value is
  accepted as truth.
- **Trigger:** Key on → ECU boots and emits one frame with a settling or corrupted
  MAP value at RPM 0 → driver turns straight to crank (a single continuous key
  motion, which is how most people start a car). The reference is now wrong and
  frozen. The low-pass tracking that would have corrected it at `:277-280` only
  runs while RPM is still 0, and never gets the chance.
- **Consequence:** Every boost reading for the entire drive carries a fixed offset,
  displayed to 0.1 PSI precision with no indication that it is wrong. This is the
  silent-wrongness case: the gauge does not fail, it lies plausibly. If the offset
  reads **low**, a driver watching for over-boost sees 12 PSI while the engine
  makes 15.6 PSI. Every logged `BOOST` column inherits the same offset, so the
  tuning data is wrong too — and unlike the display, the log is what someone
  re-jets an engine from later. The MAP page's vacuum/boost colour boundary
  (`ui.py:90-98`) shifts with it as well.
- **Evidence:** Executed the extracted capture logic. A single 60.0 kPa frame
  inside the window:
  ```
  locked baro_x10 = 600 (=60.0 kPa)
  MAP 101.3 kPa: dash shows +6.0 PSI, truth +0.0 PSI, error +6.0 PSI
  MAP 205.0 kPa: dash shows +21.0 PSI, truth +15.0 PSI, error +6.0 PSI
  ```
  The low-pass itself is correct and converges properly when it gets to run
  (1013 → 841 in 28 frames ≈ 1.6 s at 18 Hz; the `step == 0` nudge at `:278-279`
  correctly prevents the floor-shift stall, and negative deltas floor to −1 so
  downward tracking cannot stall either — I checked both directions).
- **Fix:** Two changes in `_decode`. (1) Require N consecutive qualifying frames
  (N ≈ 8, under half a second at 18 Hz) that agree within a few tenths of a kPa
  before the initial lock, or take their median — a settling ECU will not produce
  eight consecutive agreeing garbage frames. (2) Surface the state: the reference
  is already plumbed to `DashUI.update` as `baro_x10` but is only ever used to
  recolour MAP. Show "BARO 84.2" on the Boost page, and mark it visually when the
  dash is still running on `BARO_FALLBACK_KPA` — the user currently has no way to
  distinguish a locked reference from a sea-level guess. Tightening
  `BARO_MIN/MAX_KPA` is not a fix; the window has to stay wide enough for real
  altitude range.
- **Confidence:** Verified — the mechanism is executed above. The *frequency* of
  garbage frames from a booting Microsquirt is **UNVERIFIED**; a candump across
  ten key-on cycles would settle whether this is a once-a-year event or a
  once-a-month one. It does not change the fix.

### [High] F-04 — Stop enumerating the whole log directory on every engine start

- **Location:** `datalog.py:89-106` (`_next_log_path`), called from `_start`
  (`:110`) on every engine start.
- **What's wrong:** `os.listdir(config.LOG_DIR)` materialises a Python list of
  every filename in `/sd/logs/` in order to find the highest existing number. The
  directory grows by one file per engine start, forever — nothing prunes it — while
  the heap it must fit in does not grow. PERFORMANCE.md measures ~24 KB free after
  startup.
- **Trigger:** Normal use, guaranteed to arrive. Every engine start adds a file.
  At a rough 40 bytes per filename string plus list overhead, ~24 KB of free heap
  is exhausted somewhere in the low hundreds of files. A daily driver with four
  starts a day reaches several hundred files in well under a year.
- **Consequence:** `MemoryError` — which `_start()` does **not** catch, because its
  handler is `except OSError` (`:125`) — propagates through `logger.update()` into
  the unguarded main loop (F-01) and kills the dash. The failure lands at the worst
  possible moment: the instant the engine starts, every time, permanently, and it
  presents to the user as "my dash suddenly stopped working" with no clue that a
  full log directory is the cause. Recovery requires deleting files on a PC.
- **Evidence:** `datalog.py:98` `for name in os.listdir(config.LOG_DIR):` — the
  full list is built before iteration. `datalog.py:125` `except OSError as e:` —
  `MemoryError` is not an `OSError` in CircuitPython, so the module's own
  "disable logging and carry on" policy does not engage.
- **Fix:** Two independent changes, both worth making. (1) Remember the last
  session number in an instance attribute and only scan the directory once per
  boot — this alone removes the per-start cost. (2) Better: stop scanning
  altogether. Probe forward from the remembered number with
  `os.stat(candidate)`/`OSError` until a name is free, which is O(1) per start and
  allocates nothing meaningful. Also broaden `_start`'s handler from `except OSError`
  to `except Exception` so any future allocation failure degrades to `NO SD`
  instead of taking down the display. Consider a documented file cap.
- **Confidence:** Verified for the mechanism (unbounded list, wrong exception
  class). **INFERRED** for the exact file count at which it fails — that depends
  on CircuitPython's string allocation granularity and live heap at that moment.
  Testing is easy and does not need the car: create 300, then 600, then 1,000
  dummy `logNNN.csv` files on a card and watch `mem_free` with `DEBUG = True`.

### [Medium] F-05 — Alert on conditions the driver is not currently looking at

- **Location:** `ui.py:871-878` (page dispatch); `ui.py:883-941`
  (`_render_single_gauge`, which only ever renders `page`).
- **What's wrong:** Only the current page's channel is evaluated for alert state.
  Nine other channels are being updated in `EcuData` and rendered to nothing. There
  is no global alert scan, no persistent warning banner, no auto-jump to a page in
  alarm, and no audible output (the hardware has none, but the display could
  flash).
- **Trigger:** Sit on any page — the RPM page is the default at `ui.py:731`, and the
  Boost or Overview page is where an enthusiast will actually park — while coolant
  climbs past `CLT_RED_F` (230 °F).
- **Consequence:** The one condition this dash exists to catch, on the one channel
  where catching it late destroys an engine, is invisible unless the driver happens
  to be on page 4. The Overview page mitigates this for its four channels
  (Boost/CLT/AFR/Battery) — but MAT heat-soak and RPM over-rev are not among them,
  and Overview is one of ten pages the user must have navigated to.
- **Evidence:** `_render_single_gauge` takes `page` and indexes `ecu` by it
  exclusively (`ui.py:885-886`). No code path anywhere reads a channel that is not
  on the visible page — `grep` for `NUM_PARAMS` in `ui.py` returns only the page
  arithmetic in `config.py`.
- **Fix:** Add a per-channel alarm threshold table to `config.py` (the `*_STOPS`
  tables already encode where "red" starts — derive from them rather than
  duplicating, or F-14's configuration-sprawl risk applies). On each render tick,
  scan all channels; if any is in alarm, show a persistent banner naming the
  channel and value ("CLT 238") in the existing `warning_label` slot, on every
  page including Overview and Beam. Naming the channel matters — an unlabelled
  warning light sends the driver hunting through ten pages.
- **Confidence:** Verified by reading. This is a design gap, not a code defect —
  the author may have decided a glance-dash should stay simple. Recorded at Medium
  because it defeats the stated purpose of a monitoring instrument; downgrade it
  freely if it was a deliberate call.

### [Medium] F-06 — Do not encode alert state in colour alone

- **Location:** `ui.py:115-117` (`param_color`), `:890-903` (value colour),
  `:463-481` (shift-light bulbs), `:508-528` (Overview), `config.py:186-227`
  (all `*_STOPS` tables).
- **What's wrong:** Every alert level in the system is communicated by hue on a
  blue→green→yellow→red ramp, and by nothing else. The digits, their size, their
  position, and their layout are identical whether coolant is 180 °F or 240 °F.
- **Trigger:** Any user with red/green colour vision deficiency (~8% of men, and
  this is a male-dominated hobby) reading any gauge. Also any user in direct
  sunlight, where a 2.4" TFT's colour saturation degrades far faster than its
  luminance contrast.
- **Consequence:** For those users the entire alerting mechanism of the dash is
  inert. They see a number; they must know the safe range from memory, which is
  the situation before installing a colour-coded dash at all. This is an
  accessibility defect that happens to also be a safety-margin defect.
- **Evidence:** `_render_single_gauge` writes `self.value_label.color = color`
  (`ui.py:902`) and never changes `text` for alarm state — the only text change is
  `"---"` when stale (`:893`). Nothing in the repo flashes, inverts, changes scale,
  or appends a word on alarm. The green (`0x00FF00`) and red (`0xFF0000`) stops
  used across all six tables have near-identical luminance, so the two most
  important states are the two hardest to tell apart without hue.
- **Fix:** Add one non-colour channel and the finding closes. Cheapest inside the
  existing architecture: append a suffix to the value text at alarm level (the
  change-gating already re-renders the label on any value change, so this costs
  nothing extra), e.g. `238!`, or blink the value by toggling `hidden` on alternate
  render ticks above the red threshold. Combine with F-05's banner and both are
  solved together.
- **Confidence:** Verified by reading the colour tables and render path. Real-world
  legibility in sunlight is **UNVERIFIED** — I have no panel.

### [Medium] F-07 — Gate decoded values for plausibility; one bad frame poisons PEAK forever

- **Location:** `canbus.py:245-310` (`_decode`, no range checks except baro),
  `canbus.py:114-129` (`EcuData.set`, unconditional peak/low/avg update).
- **What's wrong:** Decoded values are stored with no plausibility check of any
  kind. `set()` then folds every sample into `peak_x10` and `low_x10`, which by
  design survive staleness and reset only at power-off — and there is no UI
  affordance to reset them. So a single corrupt frame is not a one-frame glitch; it
  is permanent for the rest of the drive. This is the CAN trust boundary (§8) and
  it is unvalidated.
- **Trigger:** One corrupted frame that passes CRC and the hardware ID filter — an
  EMI hit on an unterminated or untwisted run, a marginal transceiver during
  cranking, a bus glitch from another node. Also, benignly and more commonly: the
  ECU's own start-up values before sensors settle. MAP is `u16`, so `0xFFFF`
  decodes to 6553.5 kPa.
- **Consequence:** `PEAK 6553.5` on the MAP page, or `PEAK 21.0` on Boost, for the
  rest of the drive with no way to clear it short of power-cycling — which also
  ends the datalog session. The AFR page is the worst case: `LO` shows the
  *lowest* AFR seen, so a single pre-start frame reading 0.0 pins `LO 0.0`
  permanently, and the readout the driver would use to spot a dangerous lean
  excursion becomes decorative. The corrupt value is also written to the CSV row
  it lands in (`datalog.py:180`), so the log carries it too.
- **Evidence:** `_decode` performs `ecu.set(...)` directly from `struct.unpack`
  output for MAP/RPM/CLT/TPS/MAT/AFR/battery with no bounds test — the *only*
  plausibility window in the file is `_BARO_MIN_X10 <= map_x10 <= _BARO_MAX_X10`
  at `:271`, and it guards the baro capture, not the stored MAP. `EcuData.set`
  at `:123-127` updates peak and low unconditionally. `ui.py:920-924` reads
  `peak_x10` straight to the label.
- **Fix:** Add per-channel `(min, max)` sanity ranges to `config.py` beside the
  existing bar ranges, and have `_decode` drop out-of-range samples entirely
  (do not clamp — clamping invents a plausible number, which §6 rates worse than
  rejecting). Separately, give peaks a reset: a long-press, or a tap on the peak
  line, calling a new `EcuData.reset_stats()`. The touch state machine already
  distinguishes press from release (`touch.py:96-99`) so hold-detection is a small
  addition.
- **Confidence:** Verified by reading. Whether the author's Microsquirt actually
  emits pre-settle garbage is **UNVERIFIED** — a candump across key-on would show
  it. The permanence of the poisoning, once it happens, is certain from the code.

### [Medium] F-08 — Document (or gate) the fact that the dash transmits on the vehicle bus

- **Location:** `canbus.py:177-180` (`canio.CAN(...)`, `silent` not passed →
  defaults to `False`); `README.md:60-74` (wiring/termination section).
- **What's wrong:** The controller is opened in normal mode, so the dash actively
  drives the bus: it ACKs every frame and, if it decodes errors, transmits error
  frames. With `auto_restart=True` (`:179`), a controller that reaches bus-off
  restarts and resumes doing so indefinitely. The code and docs both describe this
  device as a passive listener; electrically it is not one.
- **Trigger:** Baud rate mismatch (the README lists this as a possible
  misconfiguration and predicts only "every page shows `---`"), CANH/CANL swapped,
  or missing termination causing marginal sampling. A node that cannot decode
  frames at the configured bit rate signals errors continuously.
- **Consequence:** On the two-node bus the README describes (ECU + dash), the ECU
  is broadcasting to nobody and the practical result is the blank dash the README
  predicts. On a bus carrying **functional** traffic — an MS3 with a CAN expansion
  board, a CAN wideband controller, a CAN-connected ignition or transmission
  controller — a misconfigured dash can drive those nodes error-passive and disturb
  communication the running engine depends on. This is the one path in this project
  that can reach out and affect something other than itself, which is why it is
  here despite requiring a misconfiguration to trigger.
- **Evidence:** `canio.CAN(tx=board.CAN_TX, rx=board.CAN_RX, baudrate=..., auto_restart=True)`
  — no `silent` argument. Confirmed against the CircuitPython `canio` documentation:
  `silent` defaults to `False`, and when `True` "the `tx` pin is always driven to
  the high logic level. This mode can be used to 'sniff' a CAN bus without
  interfering." (The same doc confirms the SAM E5x supports two listeners with 8
  filter blocks, so the four `canio.Match` filters at `canbus.py:186-191` are
  comfortably within budget — that part is sound.)
- **Fix:** **Do not simply set `silent=True`** — on the two-node bus this project
  targets, the dash is the ECU's only ACK source, and a silent dash would drive the
  *ECU* error-passive. That trade-off is the finding. Recommended: (1) document it
  in the README's wiring section — one paragraph saying the dash is an active bus
  participant and that a baud mismatch can disturb other nodes, not merely blank
  the display; (2) expose `CAN_SILENT_MODE = False` in `config.py` with that
  explanation, so someone adding this to a busy multi-node bus can choose; (3)
  consider surfacing repeated bus-off transitions on screen rather than silently
  auto-restarting, since today `bus_ok` only distinguishes states and the driver
  cannot tell a dead bus from one the dash itself is disrupting.
- **Confidence:** Verified for the code and the API semantics (both read directly).
  **UNVERIFIED:** whether a real Microsquirt bus in the field carries functional
  traffic that would actually be disturbed — that is installation-specific, which
  is exactly why it belongs in the README.

### [Medium] F-09 — Bound the running-average accumulators

- **Location:** `canbus.py:128-129` (`avg_sum`/`avg_count` in `set()`),
  `canbus.py:140-146` (`average_x10`), consumed only at `ui.py:915-918`.
- **What's wrong:** `avg_sum[param] += v_x10` accumulates without bound, for all
  eight channels, forever. On CircuitPython's 32-bit builds small integers are
  31-bit; past ~2³⁰ the value promotes to an arbitrary-precision integer and every
  subsequent `+=` allocates a new heap object. The average is only ever *displayed*
  for Battery (`ui.py:914-918`) — the other seven accumulators exist solely to
  overflow.
- **Trigger:** Driving for a while. Computed from the accumulation rate:

  | Channel state | Small-int limit reached after |
  |---|---|
  | RPM 6000 @ 18 Hz | ~16.6 min |
  | RPM 3000 @ 18 Hz | ~33 min |
  | RPM 800 (idle) @ 18 Hz | ~2 hours |

- **Consequence:** Not wrong output — the arithmetic stays exact — but a steady
  drip of heap allocation on the CAN decode path, on a board with ~24 KB free.
  This directly contradicts the design contract PERFORMANCE.md states ("Steady-state
  allocation ≈ 0", "integer-only, allocation-free") and undermines the reasoning
  behind "Periodic collection is not needed". PERFORMANCE.md already reports
  occasional 125–177 ms GC spikes on a live bus; this makes them more frequent as
  uptime grows. A 177 ms freeze is 3–4 dropped frames on a 20 Hz dash.
- **Evidence:** Computed from the code; the table above is executed output. The
  contradiction is between `canbus.py:128` and `PERFORMANCE.md:118-131`. Note the
  running average is also conceptually odd where it *is* used: Battery's `AVG`
  weights every sample since power-on equally, so a long idle dominates the reading
  the driver sees after an hour of driving.
- **Fix:** Only accumulate for channels that need it — guard with
  `if param == config.PARAM_BATT:` — which removes seven-eighths of the problem for
  free. Better, and it fixes the weighting too: replace the sum/count pair with a
  shift-based exponential moving average, the same integer technique already used
  and proven for the baro low-pass at `canbus.py:277`. `low_x10` deserves the same
  treatment: it is maintained for all eight channels and read only for AFR
  (`ui.py:907`, `:399`).
- **Confidence:** Verified for the arithmetic and the code path. **INFERRED** for
  the 31-bit small-int boundary on this specific CircuitPython build — running with
  `DEBUG = True` and watching `mem_free` decline over a 30-minute drive would
  confirm it on the actual hardware in one session.

### [Medium] F-10 — Catch more than `OSError` when loading the splash bitmap

- **Location:** `ui.py:746-757` (`_show_splash`), called from `DashUI.__init__`
  at `:639`, which is the *first* thing `code.py` constructs.
- **What's wrong:** The handler is `except OSError`, which covers a missing file.
  `displayio.OnDiskBitmap` raises `ValueError` for a BMP whose encoding it cannot
  parse — the case the README explicitly warns users about — and that is not
  caught. The exception escapes `DashUI.__init__`, escapes `code.py` (which wraps
  only `CanBus()` in a handler, at `:44-46`), and the VM exits before the dash ever
  starts.
- **Trigger:** Drop a 24-bit or 32-bit `splash.bmp` on the CIRCUITPY drive — which
  is exactly what a user gets by exporting a PNG from any normal image editor. The
  README anticipates this at line 163: "displayio's `OnDiskBitmap` doesn't reliably
  read 24/32-bit BMPs", and the code comment at `config.py:274` promises the file
  is "silently skipped if absent". Absent is handled; malformed is not.
- **Consequence:** A dash that will not boot at all — black screen, no error on the
  panel — caused by an optional cosmetic file. The user's most likely diagnosis is
  a hardware fault (the README's "Screen stays white / blank" row sends them to
  reseat the FeatherWing and lower `TFT_BAUDRATE`), and nothing points at the
  splash image. Recovery requires connecting a PC and reading the serial console.
- **Evidence:** `ui.py:756` `except OSError as e:`. `code.py:38` `dash = ui.DashUI()`
  is unguarded — the only `try` in `code.py` is around `canbus.CanBus()` at `:43-46`.
- **Fix:** Change to `except Exception` in `_show_splash` — the splash is
  decorative and no failure of it justifies not booting. The comment at `:747-748`
  already states the intent ("silently skip if absent"); widen it to "silently skip
  if absent *or unloadable*". While there, consider guarding `DashUI()` construction
  in `code.py` the way `CanBus()` is, though a genuine display failure leaves
  nowhere to report it.
- **Confidence:** Verified for the code path. **INFERRED** that CircuitPython 10.x
  raises `ValueError` specifically for unsupported BMP bit depths — the exception
  class varies by version. This does not affect the fix, which broadens the catch
  regardless.

### [Medium] F-11 — Validate `config.py` at startup instead of crashing later

- **Location:** `config.py` (whole file, no validation); the crash sites are
  `touch.py:67-69`, `ui.py:217` / `:562` / `:553-554`.
- **What's wrong:** `config.py` is presented to users as the file to edit — "If you
  change hardware or ECU settings, this should be the only file you need to touch"
  (`config.py:14-15`) — and nothing checks any of it. Several single-value edits
  produce a hard crash at a *later* moment, in a different file, with a traceback
  that does not name the setting.
- **Trigger:** Concrete cases traced:
  - `TS_RAW_X_MIN == TS_RAW_X_MAX` → `ZeroDivisionError` at `touch.py:67-69`, on
    the first screen tap. The README tells users to *swap* these two values to fix
    reversed navigation (line 187), so this file is actively edited by people
    following instructions.
  - Any bar's min == max (e.g. `CLT_BAR_MIN_F == CLT_BAR_MAX_F`) → `_span_x10 == 0`
    → `ZeroDivisionError` at `ui.py:217`, on the first render of that page only.
    A dash that runs fine until you navigate to page 4.
  - `SHIFT_BAR_MAX_RPM = 0` → `ZeroDivisionError` at `ui.py:469`.
  - `TAP_ZONE_FRACTION` outside 0..1, or `STALE_TIMEOUT_MS = 0` → no crash, silent
    misbehaviour (navigation dead in one direction; everything permanently stale).
- **Consequence:** Via F-01, each of these kills the dash rather than reporting the
  problem, and the delayed ones ("works until I visit the Coolant page") are
  genuinely hard for a hobbyist to attribute. `BASE_CAN_ID` is the honourable
  exception: an out-of-range value makes `canio.Match` raise inside `CanBus()`,
  which `code.py` catches and reports as a red `CAN INIT FAILED` screen. That is
  the pattern the rest of the config deserves.
- **Evidence:** `touch.py:67-69` divides by `config.TS_RAW_X_MAX - config.TS_RAW_X_MIN`
  with no guard. `ui.py:217` divides by `self._span_x10`, set at `:210` from
  `int(vmax*10) - int(vmin*10)`. No file in the repo contains a validation function
  — `grep` for `raise`, `assert`: zero matches in the entire tree.
- **Fix:** Add a `validate()` function to `config.py`, called once from `code.py`
  before constructing anything, asserting the handful of invariants that matter:
  non-zero spans on every bar range and the touch calibration, `0 <= BASE_CAN_ID <= 2044`,
  positive timing intervals, `0.0 < TAP_ZONE_FRACTION < 1.0`, `decimals in (0, 1)`
  for every `PAGES` entry. On failure, call `dash.show_fatal()` with the offending
  setting's name — the fatal screen already exists and is exactly right for this.
  Fail loudly at startup, at the boundary, naming the input: this is §11's
  "refuse to run misconfigured" and it costs about 20 lines.
- **Confidence:** Verified — each divisor was traced to a config value with no
  guard in between.

### [Medium] F-12 — There is no automated test of any kind

- **Location:** Whole repository. No test files, no test framework, no CI.
- **What's wrong:** A codebase whose core risk is fixed-point unit conversion —
  kPa↔PSI, ×10 scaling, signed 16-bit wire fields, pixel mapping, tick rollover —
  has zero executable verification. Every claim of correctness rests on reading and
  on the author's bench testing of one vehicle. §13 asks whether a test would have
  caught each High finding; here the answer is "there are no tests" four times.
- **Trigger:** Any future change, including any change made in response to this
  report. This is the finding that determines whether the others stay fixed.
- **Consequence:** Regressions ship silently. The most dangerous class — a unit or
  scaling error — produces a plausible-looking number, which is precisely what no
  human notices in review. The commit history already contains one such bug found
  only on the bench (`5519c0a`, and the comment at `ui.py:131-135` describing an
  earlier AFR decode that merged two bytes into one word).
- **Evidence:** No `test_*.py`, no `tests/`, no `pytest.ini`/`tox.ini`, no
  `.github/`. Verified by directory listing.
- **Fix:** The pure logic is already cleanly separated from hardware and needs no
  device to test. Note that `code.py` shadows the stdlib `code` module, which
  breaks host tooling run from the repo root (see I-01) — put tests in a `tests/`
  subdirectory and run them from there. Concrete tests to write, in priority order:
  1. `format_x10` — exhaustive over −2000..2000 for both `decimals` values;
     assert round-half-away-from-zero symmetry (`format_x10(-15,0) == "-2"` and
     `format_x10(15,0) == "2"`) and that no output ever contains `"-0"`.
  2. `ticks.diff` — across the 2²⁹ wrap: `diff(5, TICKS_MAX-4) == 10`,
     `diff(a,b) == -diff(b,a)`, and monotonicity for gaps under the half-period.
  3. Baro capture (F-03) — feed a frame sequence with one garbage value followed by
     engine start; assert the reference is *not* the garbage value. This test fails
     today, which is the point.
  4. Boost round-trip — for MAP 20..300 kPa, assert
     `|derived_psi - (map-baro)/6.894757| <= 0.05` across the full domain.
  5. Bar pixel mapping — `_FillBar._px` and `_AfrBar._beam_x` at empty, min, max,
     below-min, above-max, and exactly-centre; assert `0 <= px <= BAR_W` always
     and that AFR 14.7 lands on the centre pixel.
  6. `EcuData` — `is_stale` at the exact timeout boundary; peak/low with a single
     out-of-range sample (F-07); `average_x10` with zero samples returns `None`.
  7. Log path selection (F-04) — with 0, 1, and 999 existing files.
- **Confidence:** Verified.

### [Low] F-13 — Shift-light bulb colours are off by one segment

- **Location:** `ui.py:421-424`.
- **What's wrong:** Bulb `i` is coloured for RPM `i * (span/n)` but does not light
  until RPM reaches `(i+1) * (span/n)`. Every bulb therefore displays the colour of
  the RPM one segment below where it illuminates.
- **Trigger:** Watch the RPM page while revving.
- **Consequence:** Cosmetic, but it blunts the one thing a shift light exists to
  do. The first bulb lights at 1000 RPM showing the *idle* blue rather than green,
  and the last bulb — the "shift now" bulb, lighting at 7000 RPM — renders
  `FF1F00` instead of pure red, because it is coloured for 6000 RPM. The bar never
  reaches full red at redline.
- **Evidence:** Executed the extracted constructor:
  ```
  bulb 0: lights at 1000 rpm, colored for 0 rpm -> 4080FF (should be 00FF00)
  ...
  bulb 6: lights at 7000 rpm, colored for 6000 rpm -> FF1F00 (should be FF0000)
  ```
- **Fix:** `ui.py:423` — change `i * (span / n)` to `(i + 1) * (span / n)`. One
  character class of change; verified output above is the "should be" column.
- **Confidence:** Verified by execution.

### [Low] F-14 — The Overview and Beam pages show bare numbers

- **Location:** `ui.py:494-504` (Overview cells), `ui.py:539-548` (Beam rows).
- **What's wrong:** Both multi-channel pages render a name and a value with no
  unit. The single-gauge pages have a dedicated units line (`ui.py:800`); these do
  not.
- **Trigger:** Navigate to Overview or the Beam page.
- **Consequence:** "BOOST 12.3" — PSI or kPa? "COOLANT 195" — °F or °C? The
  ambiguity is worst on exactly the page designed for a fast glance, and worst for
  the audience the README courts, since MegaSquirt users routinely run mixed unit
  systems (this dash reads kPa for MAP and PSI for boost on adjacent pages). §14 is
  blunt about bare numbers, and the rest of this project already gets it right.
- **Evidence:** `_Overview.__init__` appends a name label and a value label per
  cell, no third label. `_BeamGauge.__init__` likewise: `text=name` at `:542`, then
  a value label at `:544-547`.
- **Fix:** Append the unit to the name label — `"BOOST PSI"` from
  `config.PAGES[param][1]`, which is already carried and already correct. The name
  labels are static, so this costs nothing at runtime and no new widgets.
- **Confidence:** Verified by reading. On-panel legibility of the longer string at
  `GRID_NAME_SCALE = 2` is **UNVERIFIED** — 240 px wide, two columns; may need
  scale 1 for the unit suffix.

### [Low] F-15 — Remove the unused `board` import in `datalog.py`

- **Location:** `datalog.py:34`.
- **What's wrong:** `import board` is never used — the SPI bus is passed in from
  `code.py` and the CS pin comes from `config.SD_CS_PIN`.
- **Trigger:** Import time, every boot.
- **Consequence:** Negligible: a little RAM and import time on a board where both
  are scarce. Reported because it is the only automated-tooling hit in the entire
  repository and it should not be lost in the noise — there is no noise.
- **Evidence:** `pyflakes` (run from outside the repo root, see I-01):
  ```
  datalog.py:34:1: 'board' imported but unused
  ```
  This was the *only* diagnostic across all eight files.
- **Fix:** Delete the line.
- **Confidence:** Verified by tooling.

### [Low] F-16 — A sub-threshold press re-reads the touch point every poll

- **Location:** `touch.py:105-111`.
- **What's wrong:** When the panel reports `touched` but pressure is at or below
  `TOUCH_PRESSURE_THRESHOLD`, the function returns without leaving `_IDLE`. The
  next poll repeats the full `self._touch.touch` read — which the module's own
  docstring identifies as the one allocating call (`touch.py:19-21`) — 50 times a
  second for as long as the condition persists.
- **Trigger:** Anything resting lightly on the panel; sustained vibration on a
  car-mounted display; a user pressing too gently.
- **Consequence:** ~50 small dict allocations per second on a ~24 KB heap,
  contradicting "a handful of bytes per user interaction, nothing per-loop". Same
  class of problem as F-09 and the same effect: more GC pauses. Also note
  `TOUCH_PRESSURE_THRESHOLD` defaults to 100, identical to the driver's own
  threshold, and the comparison is `<=` — so a press at exactly the documented
  minimum is rejected.
- **Evidence:** `touch.py:110-111` returns `0` without assigning `self._state`;
  the `_IDLE` branch at `:102-103` re-enters on the next tick.
- **Fix:** On a sub-threshold press, latch a `_REJECTED` state that clears on
  release, so the point is read at most once per physical contact. Or compare
  against `<` and document that the config value must exceed the driver's own
  threshold to have any effect.
- **Confidence:** Verified by reading.

### [Low] F-17 — README overstates `NO CAN` banner coverage

- **Location:** `README.md:41-42` and `:131-132` vs `ui.py:822-825` and `:926-931`.
- **What's wrong:** The README states that a stale channel shows `---` "plus a
  `NO CAN` banner (top-right)" without qualification. The banner is raised only in
  `_render_single_gauge`; `_apply_page_chrome` explicitly *clears* it when entering
  the Overview or Beam page and nothing re-raises it there. `bus_ok` is never
  consulted on those two pages at all.
- **Trigger:** Sit on the Overview or Beam page and disconnect CAN.
- **Consequence:** Four `---` cells and no banner. The user sees dashes and must
  infer the cause; the instant bus-off signal that `bus_ok` provides (the whole
  point of checking controller state rather than waiting out the 1 s timeout) is
  unavailable on 2 of 10 pages, including the one page most likely to be left on
  screen. Minor because `---` still communicates staleness.
- **Evidence:** `ui.py:822-825`:
  ```python
  if is_special:
      # ...the corner banner is single-gauge-only, so clear it on entry.
      self.warning_label.text = ""
  ```
  The code comment is honest about the behaviour; the README is what disagrees.
- **Fix:** Either raise the banner on all pages from a channel-independent check
  (`not bus_ok or all channels stale`) — which F-05's global scan would provide
  anyway — or correct the two README lines to say the banner is shown on
  single-gauge pages. Fixing the code is better than fixing the doc here.
- **Confidence:** Verified by reading both.

### [Low] F-18 — A failed touch probe is invisible on the panel

- **Location:** `touch.py:44-50`; contrast `datalog.py:75-76` → `STATE_NO_SD` →
  `ui.py:861-863`.
- **What's wrong:** When the TSC2007 is absent (a V1 FeatherWing, a bad solder
  joint), `TouchNav.__init__` prints to the USB serial console and sets
  `self._touch = None`. The display says nothing. Compare the SD card path, which
  degrades identically but *does* surface `NO SD` in the corner — the right pattern
  is already implemented in the neighbouring module.
- **Trigger:** First power-up with a V1 FeatherWing or an I2C fault.
- **Consequence:** The user taps the screen and nothing happens, with no
  explanation and no way to tell a dead touch chip from a software bug. The README
  documents the diagnosis ("check the serial console for the probe message"), which
  requires a laptop and a USB cable — for a fault the panel could report in two
  characters.
- **Evidence:** `touch.py:50` `print(...)` is the entire user-facing signal.
  `DashUI` receives no touch status: `dash.update()` is called with `log_state` but
  no equivalent for touch (`code.py:78-79`).
- **Fix:** Expose a `TouchNav.available` property, pass it to `DashUI.update`, and
  show a dim `NO TOUCH` next to the datalog indicator. Roughly ten lines, mirroring
  the `log_state` plumbing that already exists.
- **Confidence:** Verified by reading.

### [Informational] I-01 — `code.py` shadows the stdlib `code` module for host tooling

Running any host-side Python tool from the repo root fails: `pyflakes` imports
`doctest` → `pdb` → `code`, and gets this project's `code.py`, which raises
`ModuleNotFoundError: No module named 'board'`. The filename is mandated by
CircuitPython and cannot change. Worth one line in the README for contributors:
run linters and tests from outside the repo root, or from a `tests/` subdirectory
(see F-12). This cost me a tool invocation to discover and will cost every
contributor the same.

### [Informational] I-02 — Manual refresh is load-bearing for shared-SPI safety

`auto_refresh = False` (`ui.py:637`) is justified in PERFORMANCE.md purely on
tearing and SPI-timing grounds. It is also what keeps display refreshes from
interleaving with SD card transactions on the *same* SPI bus (`code.py:51` passes
`board.SPI()` to the logger). A future contributor who re-enables auto-refresh for
convenience would reintroduce that hazard without any comment warning them.
Add one sentence to PERFORMANCE.md's display section.

### [Informational] I-03 — The repo discloses the author's approximate location

`PERFORMANCE.md:63` states the baro reference "locked 84.2 kPa at ~5,000 ft" and
commit `63bfee1` records "measured local baro (84.1 kPa)". For a repository
intended for publication this narrows the author's home elevation, and the commit
is in history where editing the file will not remove it. Almost certainly
acceptable — it is also genuinely useful validation evidence, which is why it is
Informational and not a finding — but it should be a decision rather than an
accident.

### [Informational] I-04 — Local-only pre-refactor snapshot

`backup/pre-refactor/` holds a 1,771-line single-file ancestor. Correctly
gitignored, untracked, and excluded from this audit per §9. Nothing in it is on
fire; it is simply a second copy of logic that has since been fixed in the modules
(F-13 and the AFR decode bug, at minimum). The `pre-refactor` git tag already
preserves this history, so the directory is redundant and could be deleted without
loss.

### [Informational] I-05 — No changelog, no security contact, no `Known limitations`

For a project actively soliciting forks and contributions: there is no CHANGELOG,
no `SECURITY.md`, and the README links to a `#known-limitations` anchor
(`README.md:23`) for a section that does not exist. The last is a broken link in
the "Why this exists" pitch — the first thing a prospective contributor reads.

### [Informational] I-06 — MegaSquirt CAN is unauthenticated by design

Any node on the bus can transmit IDs 1512–1515 and the dash will believe it.
This is a property of the protocol, not a defect in this code, and there is no
fix available at this layer — noted only so it is not rediscovered as a finding
later. It is also why F-07's plausibility gating is the right mitigation: you
cannot authenticate the sender, so sanity-check the value.

---

## Clean bill

Subsystems examined and found sound. These are the places *not* to look.

- **`ticks.py`** — rollover-safe arithmetic is correct. Executed: `diff(5, TICKS_MAX-4) == 10`
  across the wrap, sign symmetry holds, and every caller correctly uses
  `diff(now, then) >= interval` rather than absolute comparison. I checked all
  nine call sites. The rationale comment for choosing this over `time.monotonic()`
  is accurate.
- **`format_x10`** (`canbus.py:68-89`) — correct for both decimal modes across
  positives, negatives, and zero, with symmetric round-half-away-from-zero and no
  float round-trip. Executed across the boundary values including ±0.5 cases and
  the full int16 range.
- **CAN receive path** (`canbus.py:220-241`) — non-blocking contract honoured
  (`timeout=0`), drain bounded by `CAN_MAX_FRAMES_PER_UPDATE`, RTR/runt frames
  correctly skipped via `getattr(msg, "data", None)` and the `len < 8` check.
  Four exact hardware filters fit within the SAM E5x's documented 8 filter blocks.
- **Fixed-point data model** — keeping wire values at ×10 and formatting only at
  the edges is the right call for this platform, and it is applied consistently.
  Change-detection on exact integers is sound; no float equality comparison exists
  anywhere in the repo.
- **Render change-gating** (`ui.py`, all widget classes) — I traced the cache
  lifecycle across page switches, including hidden widgets retaining both bitmap
  contents and caches. It is consistent: a hidden page's cache correctly still
  describes its bitmap, so returning to a page repaints exactly what changed. The
  `_UNSET` sentinel at `ui.py:135` is a genuinely subtle bug fix, correctly done.
- **AFR centre-zero bar geometry** (`ui.py:318-336`) — executed across the domain;
  stoich pins to the centre pixel, both half-scales clamp correctly, out-of-range
  AFR (0.0, 25.5) maps to the bar ends without escaping bounds.
- **Baro low-pass filter** (`canbus.py:277-280`) — the arithmetic is right in both
  directions. The `step == 0` nudge correctly handles the floor-shift stall on
  small positive deltas, and negative deltas floor to −1 so downward tracking
  cannot stall. Converges 1013→841 in 28 frames (~1.6 s). Only the *initial lock*
  is the problem (F-03); the tracking is sound.
- **Boost derivation rounding** (`canbus.py:286-291`) — round-half-away-from-zero,
  correct for negative (vacuum) values, one float multiply as documented.
- **Datalogger session lifecycle** — the engine gate is correct, including closing
  the file when the RPM channel goes stale (key-off), which is the case most
  implementations miss. File numbering survives power cycles. `flush()` per row is
  the right trade-off and is documented as such. Only the write's error handling
  (F-02) and directory scan (F-04) are defective; the design is right.
- **`boot.py`** — empty on purpose, with the best comment in the repository. The
  reasoning for not remounting internal flash is correct and hard-won.
- **Secrets** — none. Manual scan of the working tree and all 41 objects in git
  history for credentials, keys, tokens, emails, absolute paths containing a
  username, and internal hostnames: zero hits. Commits are authored under a GitHub
  noreply address. Nothing to rotate.
- **Dependencies** — nothing vendored, nothing pinned to a git URL or a fork, no
  lockfile to drift. The five runtime libraries are all first-party Adafruit and
  actively maintained. No CVE surface worth reporting: this code makes no network
  calls, parses no untrusted file formats beyond a BMP, and deserializes nothing.
- **Injection surfaces** — none exist. No SQL, no shell, no `eval`/`exec`, no
  templating, no HTML, no deserializer. `grep` confirms zero matches for all of
  these across the repo.
- **Concurrency** — genuinely not applicable, and correctly so. Single-threaded
  cooperative loop, no interrupts, no callbacks, no async. One `ticks.ms()` per
  pass shared by all subsystems is the right pattern and eliminates a whole class
  of timing inconsistency. There is no shared mutable state reachable from two
  contexts because there is only one context.
- **Irreversible operations** — the only destructive-capable path (SD writes)
  never deletes, never truncates, and never reuses a filename; it has exactly one
  chokepoint with no back doors. I looked specifically for debug flags, test hooks,
  and alternate entry points and found none. This is the §7 section most projects
  fail, and this one passes on design; its problems (F-02, F-04) are error handling,
  not blast radius.

---

## What I could not verify

Each item paired with the specific evidence that would settle it.

1. **The CAN frame byte layout itself.** `config.py:53-64` cites the EFI Analytics
   "Megasquirt CAN realtime data broadcast protocol" (2016-02-17) for every offset,
   signedness, and scale. I attempted to retrieve that document and two secondary
   sources and could not obtain the field table. *Everything* this dash displays
   depends on these seven constants being right. The author reports live validation
   on a real Microsquirt, which is stronger evidence than a document — but it
   validates one firmware build. **Settles it:** a `candump` capture of IDs
   1512–1515 from a running engine alongside the same values read in TunerStudio,
   committed as a test fixture. That fixture would also unblock several of F-12's
   tests.
2. **On-screen behaviour after an unhandled exception** (F-01). Whether the panel
   retains the last rendered frame or is reset to the CircuitPython console
   determines whether F-01 is High or Critical. **Settles it:** put
   `raise RuntimeError("test")` in the main loop on the bench, power the board from
   USB with no serial terminal attached, and photograph the screen.
3. **The MicroPython small-int boundary on this build** (F-09). I computed the
   overflow timings from a 31-bit assumption. **Settles it:** run with `DEBUG = True`
   for 30 minutes on a live bus and watch whether `mem_free` trends downward.
4. **The file count at which F-04 fails.** Depends on live heap and allocation
   granularity. **Settles it:** 300/600/1000 dummy `logNNN.csv` files on a card,
   watching `mem_free` at engine start.
5. **The exception class `OnDiskBitmap` raises for a 24-bit BMP** (F-10).
   **Settles it:** copy a 24-bit BMP to CIRCUITPY and read the serial traceback.
   The fix does not depend on the answer.
6. **Everything in §14 that needs eyes on the panel.** Sunlight legibility of
   `COLOR_ARROW` (0x606060) and `COLOR_DOT_INACTIVE` (0x404040) on black; whether
   F-14's unit suffixes fit at `GRID_NAME_SCALE = 2`; whether the tap zones feel
   right with a 0.5 split and no dead zone; whether 20 Hz reads as smooth on a
   moving vehicle. I audited the UI statically only — **all of §14 is `UNVERIFIED`
   except what is derivable from the source.** No emulator substitutes for a
   FeatherWing.
7. **Whether the author's Microsquirt emits implausible frames at key-on** (F-03,
   F-07). Determines how often those findings actually bite, not whether they are
   real. **Settles it:** the same candump as item 1, captured across ten key-on
   cycles.
8. **Real-world CAN bus interference** (F-08). Whether a misconfigured dash
   disturbs anything depends on what else is on the user's bus. **Settles it:**
   deliberately set `CAN_BAUD_RATE = 250_000` on a bench bus with a scope or a
   second CAN analyser and observe the error frames.
9. **The five Adafruit library versions actually deployed.** Nothing is vendored
   and there is no lockfile, so I read the README's stated bundle (20260710) rather
   than any installed artifact. **Settles it:** `ls /Volumes/CIRCUITPY/lib` and the
   `__version__` string in each `.mpy`.
10. **Build reproducibility (§5).** "Build" here is copying files to a USB drive,
    and I could not perform it without the hardware. The README's install list
    matches the tree exactly, and the library list is specific and version-pinned
    in prose, which is as good as this deployment model allows.

---

## Prioritised remediation plan

Ordered by (consequence × likelihood) ÷ effort, not by severity alone.

### Fix before anything else

| # | Finding | Effort | Test to land with it |
|---|---|---|---|
| 1 | **F-02** — wrap SD write/flush/close in `try/except OSError`, degrade to `NO SD` | ~6 lines | Simulate a write failure with a stub file object; assert state becomes `STATE_NO_SD` and no exception escapes |
| 2 | **F-01** — `try/except` around the main loop body; arm the watchdog | ~15 lines | Bench test: inject `raise` in the loop, confirm the dash survives and the fault indicator appears |
| 3 | **F-04** — replace the per-start `listdir` with a remembered counter + `os.stat` probe; broaden `_start`'s handler to `except Exception` | ~15 lines | Path selection with 0, 1, and 999 pre-existing files |

These three are the whole "will it still be working in an hour" problem, and
together they are well under an hour of work. Do them as one commit.

### Fix this iteration

| # | Finding | Effort | Test to land with it |
|---|---|---|---|
| 4 | **F-03** — require N agreeing frames before the initial baro lock; display the reference and its locked/fallback state | ~20 lines + a UI label | Frame sequence with one garbage value then engine start; assert the reference is not the garbage value (fails today) |
| 5 | **F-10** — widen `_show_splash`'s handler to `except Exception` | 1 line | Malformed-BMP fixture; assert `DashUI()` constructs |
| 6 | **F-11** — `config.validate()` called from `code.py`, reporting via `show_fatal` | ~20 lines | Bad-config fixtures for each invariant; assert each is caught by name |
| 7 | **F-12** — stand up `tests/` with the seven suites listed in that finding | half a day | This *is* the test work; land it alongside items 4–6 so they arrive tested |
| 8 | **F-13** — one-token fix to bulb colour indexing | 1 char | Assert bulb 6's colour equals the gradient at `SHIFT_BAR_MAX_RPM` |
| 9 | **F-15** — delete the unused import | 1 line | — |

### Fix when touching that area

| # | Finding | Notes |
|---|---|---|
| 10 | **F-05** — global alert scan + channel-naming banner | Do together with F-06 and F-17; they share the mechanism, and F-17's banner fix falls out for free |
| 11 | **F-06** — add a non-colour alarm channel (suffix or blink) | Cheap once F-05's scan exists |
| 12 | **F-07** — plausibility ranges in `config.py`; peak reset via long-press | The reset gesture is the larger half; the ranges are quick and should land with F-12's boundary tests |
| 13 | **F-09** — restrict `avg_sum`/`low_x10` to the channels that use them, or switch to a shift-based EMA | Fixes the Battery `AVG` weighting problem at the same time |
| 14 | **F-14**, **F-18** — units on Overview/Beam; `NO TOUCH` indicator | Both are small and improve first-run comprehension, which matters for a project inviting new users |
| 15 | **F-08** — README paragraph on active bus participation; optional `CAN_SILENT_MODE` | Documentation first — the code change carries a real trade-off (the dash is the ECU's only ACK source on a two-node bus) and should not be made reflexively |
| 16 | **F-16** — latch a rejected-press state in the touch state machine | Trivial; do it next time `touch.py` is open |

### Accept and document

- **I-01** (`code.py` shadowing stdlib `code`) — unavoidable; add one line to the
  README for contributors.
- **I-02** (manual refresh is load-bearing for shared SPI) — one sentence in
  PERFORMANCE.md.
- **I-03** (approximate location in history) — the author's call; flagged so it is
  a decision.
- **I-04** (`backup/`) — safe to delete; the `pre-refactor` tag preserves it.
- **I-05** — fix the broken `#known-limitations` link at minimum; a CHANGELOG is
  optional at this scale.
- **I-06** — inherent to MegaSquirt CAN; F-07 is the available mitigation.
- Peaks/averages resetting only at power-off, and `flush()`-per-row costing
  throughput, are both **deliberate and correctly documented**. Leave them.

---

*This report is engineering observation. The licensing analysis in
`THIRD-PARTY-NOTICES.md` is not legal advice; have counsel review anything there
before it matters commercially.*
