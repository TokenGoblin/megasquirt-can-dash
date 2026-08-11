# Hardware bring-up checklist

For validating a build on the actual Feather M4 CAN before it goes in a car.
Written for the audit remediation change set (all 18 findings in
`AUDIT-REPORT.md`), but the shape is reusable for any change this size.

**Why this exists.** Everything in that change set was verified on desktop
Python: 154 tests, all passing, zero linter warnings. None of it has executed
on a SAME51. The test suite exercises pure logic against stubs, so there is a
whole category of failure it cannot see - CircuitPython API differences, RAM
exhaustion, pixel collisions, and how any of it *feels* at 20 Hz on a 2.4"
panel in a moving car.

Work top to bottom. Each stage adds one piece of hardware to the last, so a
failure tells you where it lives. **Do not skip to stage 6 because the car is
right there** - a fault at stage 1 is thirty seconds to diagnose on the bench
and an afternoon in a driveway.

Delete this file once you're green, or keep it for the next big change.

---

## Stage 0 - Before you plug anything in

- [ ] **Keep a known-good copy.** Copy the current contents of `CIRCUITPY` to
      a folder on your PC. If the new code won't boot, this is your way back
      in thirty seconds instead of a git checkout on a drive you can't write to.
- [ ] **Know the escape hatch.** If the board comes up unresponsive: double-tap
      RESET to enter safe mode (CircuitPython won't run `code.py`), then copy
      your backup over. Nothing in this change set can brick the bootloader.
- [ ] **Open a serial console** (Mu, `screen`, PuTTY, `tio`) *before* powering
      up. Almost every check below is confirmed by a line on serial, and
      several failures are silent on the panel by design.
- [ ] **Set `DEBUG = True`** in `config.py` for stages 1-5. Set it back to
      `False` before the car - it allocates on every print.
- [ ] Note your current free memory if you have a baseline. The pre-change
      figure in `PERFORMANCE.md` is **~24 KB free after startup**.

---

## Stage 1 - Does it boot at all?

**Hardware: Feather + FeatherWing only.** No CAN wiring, no SD card. This
stage catches import errors, config validation mistakes, and anything that
allocates too much at construction - i.e. everything that would stop the dash
existing.

- [ ] Copy the eight `.py` files to `CIRCUITPY`. (`tests/` does **not** go on
      the board - it would waste flash and never runs there.)
- [ ] Board boots to the RPM page showing `---` and a red `NO CAN` banner.
- [ ] Serial shows `startup complete, mem_free: <n>`.

**What can go wrong here, in order of likelihood:**

| Symptom | Cause | What it means |
|---|---|---|
| Red screen, `CFG: <SETTING>` | `config.validate()` rejected a setting | Serial has the full explanation under `--- config.py problems ---`. If this fires on the **shipped** config, that's a bug in my validation, not in your settings - tell me the setting name |
| Red screen, `CAN INIT FAILED` | `canio.CAN()` raised | **Most likely the `silent=` kwarg** - see below. Serial has the traceback |
| Black screen, traceback on serial | Something raised before the display existed | Read the traceback; restore the backup |
| `MemoryError` anywhere | The new labels/tables don't fit | Stage 2 covers this |

> **The `silent=` kwarg was my biggest API guess - it is now handled.**
> `canbus.py` passes `silent=config.CAN_SILENT_MODE` to `canio.CAN()`, and if
> your build doesn't accept that keyword it falls back to a plain open and
> carries on. The one exception is deliberate: if you have actually *set*
> `CAN_SILENT_MODE = True` and the build can't honour it, the open fails
> loudly rather than quietly handing you a transmitting controller you asked
> to keep quiet. With the default (`False`) there is nothing to notice.
> Covered by tests, both directions.

---

## Stage 2 - Memory and loop timing

The change set added five labels (fault counter, baro readout, touch status,
two beam unit labels) and several lookup tables. On a board with ~24 KB free
that is worth measuring, not assuming.

- [ ] `mem_free` after startup is **comfortably above 15 KB**. Note the number.
- [ ] Let it idle five minutes on the `DEBUG` line. `mem_free` should sawtooth
      shallowly or barely move - **it must not trend steadily downward.**
- [ ] `worst_loop_ms` is in the same range `PERFORMANCE.md` records (5-10 ms
      typical, occasional GC spikes).
- [ ] `loops/s` is still in the hundreds.

**If `mem_free` is much lower than before:** the label additions are the
suspect. The cheapest thing to drop is the baro readout on the Boost page.
Tell me the number and I'll trim.

**If `mem_free` trends downward over minutes:** something allocates per-tick
that shouldn't. That is a real regression - capture a few `DEBUG` lines and
I'll find it. (This is also the check that settles UNVERIFIED item 3, the
31-bit small-int question behind F-09.)

---

## Stage 3 - Display and layout

Still no CAN, no SD. Everything reads `---`, which is exactly what you want
for checking layout: the widest possible corner banners are all showing at once.

- [ ] Tap through **all ten pages**. Nothing clips, overlaps, or falls off.
- [ ] **Top row, all four indicators at once** - calculated, now pinned by a
      test, but still never seen. Worst case on a 240 px screen:

      | Indicator | x span | Gap to next |
      |---|---|---|
      | `LOG OFF` / `NO SD` | 4-46 | **4 px** |
      | `NO TOUCH` | 50-98 | **4 px** |
      | `FLT 19` | 102-138 | 26 px |
      | `BATTERY 20.0` (widest possible banner) | 164-236 | - |

      A test now asserts these can never overlap, whatever gets renamed or
      added later. But **the two 4 px gaps on the left are under one character
      cell** - tight enough that "doesn't overlap" and "reads cleanly" are not
      the same claim. That left cluster is what to look at. All four at once
      needs a card fault plus a dead touch chip plus a caught fault, so you
      may have to force it (pull the card, unplug the wing's I2C, or just
      trust the arithmetic and check the pairs you can produce).
- [ ] **Overview page** (9th): four cells now read `BOOST PSI`, `COOLANT F`,
      `AFR 1`, `BATTERY V`. Check the longer labels don't run into each other
      across the two columns.
- [ ] **Beam page** (10th): `BOOST` with a small `PSI` beside it, `MAT` with a
      small `F`. The unit sits in its own scale-1 label specifically so a wide
      value like `-14.7` can't overlap it - check that holds.
- [ ] **Boost page**: a dim `BARO 101.3 EST` in orange below the peak line.
      `EST` is correct here - with no CAN the dash has never measured baro.
- [ ] Page dots at the bottom still track the current page.

---

## Stage 4 - Touch

- [ ] Tap left/right: pages step back and forward, wrapping at both ends.
- [ ] **No `NO TOUCH` in the top-left.** If it's there, the controller wasn't
      found - check serial for `TSC2007 touch controller not found`. (That
      indicator is F-18: it used to be a serial line only.)
- [ ] **Taps feel the same as before.** This is the one to be suspicious of.
      F-16 added a bounded retry: a contact below `TOUCH_PRESSURE_THRESHOLD`
      for 5 consecutive polls (~100 ms) is written off until it lifts. I chose
      5 to let a landing finger seat while still catching something resting on
      the panel. **If light taps now get ignored, that number is too low** -
      it's `_REJECT_POLLS` in `touch.py`. Tell me and I'll raise it.
- [ ] Rest a coin or your palm on the panel for ten seconds, then tap normally.
      The tap should register immediately after you lift.
- [ ] **Hold anywhere for ~1.5 s.** Expected: the page steps once on the press
      (taps fire on the press edge - that's by design), then steps *back* when
      the hold registers. You end up where you started.
- [ ] After a hold, every `PEAK` / `LO` / `HI` / `AVG` reads `---`. That's the
      only confirmation there is, deliberately.
- [ ] **Judge the gesture.** The page-flip-and-undo is a compromise I flagged
      in the report - it was the only way to add a hold without making every
      tap wait for a release. If it feels wrong, say so; alternatives exist
      (a dedicated zone, or reset-on-engine-start).

---

## Stage 5 - SD card and datalogging

- [ ] Insert a FAT32 card. Serial:
      `SD card mounted at /sd; next log file: /sd/logs/logNNN.csv`.
- [ ] Corner reads `LOG OFF` (not `NO SD`).
- [ ] **The number continues from your existing logs.** If you have old logs on
      the card, `NNN` should be one past the highest. This is F-04's bisect
      probe replacing the full directory scan - it now runs once at boot
      instead of at every engine start.
- [ ] **Test it with a full-ish card.** Copy 300+ dummy `logNNN.csv` files on
      and reboot. It should still mount promptly and pick the right number,
      and `mem_free` should be unchanged. *This is the check that settles
      UNVERIFIED item 4* - the old code would eventually `MemoryError` here,
      at engine start, taking the whole dash with it.
- [ ] **Pull the card while it's running.** The dash must keep running with
      the corner switching to `NO SD`, and serial printing
      `Datalogging disabled (SD card write failed)`. **The gauges must not
      stop.** This is F-02, the single highest-value fix in the set - if the
      display dies here, that fix is wrong and I need to know immediately.

      The failure path is now simulated in the test suite (a fake file that
      raises `OSError` mid-session): no propagation, state drops to `NO SD`,
      no retry storm, partial rows kept, and a failing `close()` still ends
      the session. **Do it for real anyway** - what the tests cannot tell you
      is how the actual SD driver behaves when the card leaves the socket
      mid-transaction, which is a different thing from `write()` raising.
- [ ] Drop a deliberately broken `splash.bmp` on the drive (any 24-bit PNG
      renamed will do). It should be skipped silently with a serial note, and
      the dash should boot normally. This is F-10, and it also settles
      UNVERIFIED item 5.

---

## Stage 6 - CAN, decode, and the barometric lock

**Bench with a live ECU, or in the car.** Check `CAN_BAUD_RATE` matches your
ECU before you connect anything - see the wiring note in `README.md` about the
dash being an active bus participant.

- [ ] `NO CAN` clears within a second of the ECU broadcasting.
- [ ] Every channel reads plausibly. Compare against TunerStudio side by side.
- [ ] **Key on, engine off, watch the Boost page.** Within about half a second
      of frames the readout should change from `BARO 101.3 EST` (orange) to
      `BARO <your local pressure>` (dim). Boost should read exactly `0.0`.
- [ ] Note the locked value. At ~5,000 ft it should be near 84; at sea level
      near 101. **This is F-03 working**: it now needs 8 consecutive engine-off
      frames agreeing within 0.5 kPa, instead of trusting the first frame it saw.
- [ ] `DEBUG` prints `baro_x10:` and `boost_x10:` - watch them settle.
- [ ] **Start the engine.** The baro reference must freeze. Boost should go
      negative at idle (vacuum) and positive under load.
- [ ] **If it never locks** (stays `EST` through several key-ons), tell me -
      it means your ECU emits fewer engine-off frames than I assumed, and
      `BARO_LOCK_SAMPLES` needs lowering.
- [ ] **Watch for any channel stuck on `---` while the bus is healthy.** That
      would be F-07's plausibility gate rejecting a value your setup produces
      legitimately - the ranges in `SANE_RANGES` are deliberately wide, but I
      set them without seeing your data. **Expected and correct:** AFR reads
      `---` rather than `0.0` when the wideband is cold, which is what stops it
      pinning `LO 0.0` all session.

### The one thing worth capturing while you're here

If you can run `candump` (or any CAN sniffer) on IDs **1512-1515** for a few
seconds with the engine running, and note the matching TunerStudio values, that
capture becomes a test fixture. **It is the most valuable thing anyone could do
for this project.** The frame byte layout is the one piece of correctness that
154 tests still take on faith - I could not retrieve the EFI Analytics protocol
document to verify it independently. A capture across ten key-on cycles would
also settle how often a booting ECU emits implausible frames (UNVERIFIED items
1 and 7).

---

## Stage 7 - Alarms

Easiest tested by temporarily lowering a threshold rather than overheating your
engine. In `config.py`, set `CLT_RED_F` to just below your normal running
temperature, reboot, and start the engine.

- [ ] A blinking banner appears top-right naming the channel and value:
      `COOLANT 195`.
- [ ] **It's visible from every page** - tab through all ten. This is F-05,
      the whole point: an overheat used to be invisible unless you happened to
      be on that page.
- [ ] On the coolant page itself, the big number gains a trailing `!`.
- [ ] The `!` is on the Overview cell too.
- [ ] **The blink rate is legible, not annoying.** 500 ms (`ALARM_BLINK_MS`).
      Judge it in daylight - if it's distracting while driving, say so.
- [ ] **Squint test / photograph it.** The alarm must be obvious without
      relying on the red. That's F-06 - if you can't tell at a glance in a
      grayscale photo, it isn't done.
- [ ] Kill the ECU broadcast while the alarm is active: the banner must switch
      to `NO CAN` and stop blinking. A dead bus outranks an alarm, because an
      alarm derived from data that stopped arriving isn't information.
- [ ] **Put `CLT_RED_F` back to 230.**
- [ ] With the engine off, confirm nothing alarms (`ALARM_ONLY_WHEN_RUNNING`).

---

## Stage 8 - Fault containment

Deliberately break something. This is F-01, and it's the fix that decides
whether the dash survives a bad day.

- [ ] Add `raise RuntimeError("test")` as the first line inside the main loop's
      `try:` in `code.py`. Expected: red `LOOP FAULT` screen (20 faults inside
      the rolling window arrives almost instantly), serial printing
      `loop fault N : test`.
- [ ] Now make it intermittent instead. Put this **immediately after
      `now = ticks.ms()`** (it needs `now` to exist, or you'll get a
      `NameError` that the handler dutifully catches and reports as a fault -
      technically a passing test, but a confusing one):
      `if now % 5000 < 2: raise RuntimeError("test")`. Expected: a red `FLT n`
      counter appears top-center, the dash **keeps running**, and the counter
      clears itself after 5 s of quiet.
- [ ] **Remove the test raise.**
- [ ] While you're here: settle UNVERIFIED item 2. With the test raise in and
      the fatal path disabled (set `MAX_FAULTS_BEFORE_HALT` very high), pull
      the USB serial and power from a battery, then look at the screen after an
      uncaught error. I expect CircuitPython resets the display to its console
      and shows a traceback - if instead it **holds the last frame with the
      gauges still on it**, that's worse than I rated it, because frozen
      plausible numbers are more dangerous than a blank screen. Tell me which.

---

## Stage 9 - Watchdog (optional, only if you want it)

Off by default (`WATCHDOG_TIMEOUT_S = 0.0`). Two reasons, both worth
respecting: not every port exposes `microcontroller.watchdog`, and in RESET
mode the timer keeps running while you sit at the REPL, rebooting the board
every few seconds while you develop.

- [ ] Set `WATCHDOG_TIMEOUT_S = 4.0`. Serial should print
      `watchdog armed: 4.0 s`.
- [ ] If it prints `watchdog unavailable - continuing without it: ...`, the
      atmel-samd port doesn't support it. That's handled, not a failure - but
      tell me, because then F-01's hang protection genuinely isn't available
      on this board and the report should say so.
- [ ] Simulate a hang: `while True: pass` inside the loop. The board should
      reset itself after ~4 s rather than sitting dead.
- [ ] **Decide whether you want it on in the car.** With it armed, dropping to
      the REPL for development means resets every few seconds - set it back to
      0 while you work.

---

## Stage 10 - In-car soak

`DEBUG = False`, watchdog set however you decided, real drive.

- [ ] A full drive cycle: cold start, warm-up, normal driving, shutdown.
- [ ] Datalog opens on engine start and closes on shutdown; the CSV opens in
      MegaLogViewer HD.
- [ ] Boost reads 0.0 at every engine-off, including after a stop somewhere at
      a different elevation if you get the chance (that exercises the low-pass
      re-tracking).
- [ ] **No `FLT` counter appears at any point.** If one does, the serial
      console will have named the exception - that's a real bug, and the whole
      point of the counter is that you find out without losing the dash.
- [ ] Peaks look right at the end of the drive. No absurd values - that would
      mean a corrupt frame got past `SANE_RANGES`.
- [ ] **Sunlight legibility** (UNVERIFIED item 6, still entirely open): can you
      read the alarm banner, the dim `BARO` line, the `<` `>` tap hints
      (`0x606060`) and the inactive page dots (`0x404040`) in direct sun? Those
      dim greys were chosen on a bench, and a bench is not a windscreen.

---

## Reporting back

For anything that fails, the useful payload is:

1. **Which stage and checkbox.**
2. **The serial output** - most of this change set announces itself there, and
   the exception text is usually the whole answer.
3. **A photo** for anything visual. I calculated the layout arithmetic but have
   never seen this panel.
4. **The `DEBUG` line** (`loops/s`, `worst_loop_ms`, `mem_free`, `faults`) if
   it's a timing or memory question.

**Stage 2 is now the one I'd most expect to turn something up.** Five new
labels and several lookup tables against a ~24 KB heap is a real budget, and
it is the only item on this list that no amount of desktop testing can
estimate. Stage 4's feel questions (tap sensitivity, the hold gesture) come
next, because those are judgement calls I made blind.

Stages 1, 3 and 5 have since been hardened or pinned by tests - the `silent=`
keyword now falls back cleanly, the top-row layout can no longer silently
overlap, and the card-failure path is simulated end to end. Walk them anyway;
they are quick, and each one checks something the tests structurally cannot
(a real driver, a real panel, a real card leaving its socket).
