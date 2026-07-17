# Performance & Architecture Notes

How this dash stays responsive on a 120 MHz SAMD51 running interpreted Python,
and why each design decision matters **specifically on CircuitPython**. Written
for someone who wants to modify the code without accidentally undoing the
optimizations.

## The one-sentence architecture

CAN frames update a fixed-point integer store (`EcuData`) as fast as they
arrive; completely decoupled from that, a ~20 Hz render tick formats and draws
**only what actually changed** since the last paint, pushed to the panel with
one manual `display.refresh()` per tick.

## Concurrency / data-flow model

CircuitPython has no threads - this is a **cooperative, non-blocking loop**.
Each subsystem's `update()` is called every pass and internally decides (from
one shared `ticks.ms()` stamp per pass) whether its own deadline has arrived:

```
                 every pass          50 Hz            ~20 Hz           10 Hz
 ┌────────┐   ┌─────────────┐   ┌────────────┐   ┌─────────────┐   ┌────────────┐
 │  loop  │──▶│ canbus      │──▶│ touch      │──▶│ ui          │──▶│ datalog    │
 └────────┘   │ drain FIFO  │   │ poll press │   │ render tick │   │ CSV row    │
              └─────┬───────┘   └─────┬──────┘   └────▲────────┘   └────▲───────┘
                    ▼                 │                │                │
               EcuData (x10 ints, timestamps, peaks/low/avg)  ──────────┘
                                      │                │
                                      └── change_page ─┘
```

- **No `time.sleep()` anywhere in the loop path.** The loop spins at several
  hundred Hz; subsystems gate themselves.
- **All scheduling uses `supervisor.ticks_ms()` deltas** (`ticks.py`), never
  absolute comparisons: `time.monotonic()` is a float that loses millisecond
  precision within hours of uptime on this chip; ticks are integer ms and the
  delta math stays correct across the 2^29 ms wrap.

## CAN path

- **Hardware acceptance filters** (`canio.Match`, one exact match per dash ID):
  the SAME51 rejects every unrelated frame in silicon. On a busy car bus this
  is the difference between Python touching 4 IDs' worth of traffic and Python
  touching *all* of it. Never receive-and-discard in Python.
- **Non-blocking drain**: the listener is created with `timeout=0`, and each
  pass loops `receive()` until the FIFO is empty (bounded by
  `CAN_MAX_FRAMES_PER_UPDATE = 16` to cap worst-case pass time). Bursts can't
  back up; at ~200 matching frames/s and hundreds of drain passes/s, the FIFO
  is essentially always near-empty.
- **`struct.unpack` with precompiled `'>'` format constants** decodes the four
  packed big-endian words of frame base+0 in one native call instead of eight
  Python byte reads and shifts.
- **Bus-off resilience**: `auto_restart=True` lets the controller recover from
  bus-off by itself, and `CanBus.bus_ok` (checked each render tick) raises the
  `NO CAN` banner immediately on a failed bus state instead of waiting out the
  1 s stale timeout.
- **Self-calibrating baro reference**: boost's zero point is captured from
  engine-off MAP (which *is* local baro, read by the same sensor - so sensor
  offset error cancels in the subtraction). Gated on RPM == 0 from the same
  frame plus a 55-110 kPa plausibility window, low-pass-tracked while parked,
  frozen while running. Integer-only and allocation-free; validated live
  (locked 84.2 kPa at ~5,000 ft, boost exactly 0.0 engine-off).

## Fixed-point (x10 integer) data model

MegaSquirt already broadcasts most channels as `value * 10` integers, so the
store keeps them exactly that way (`EcuData.value_x10`, RPM normalized to x10
too). Why this matters on CircuitPython:

- **Exact change detection**: "did this channel change since last paint?" is a
  small-int compare (`==`), free of float noise, and small-int math doesn't
  heap-allocate in CircuitPython.
- **Strings are built only at the edges** (render tick / datalog row), and only
  when the underlying int changed. `format_x10()` splits the fixed-point int
  directly (`"{}.{}".format(v // 10, v % 10)`) - no float round-trip.
- The single float op in the whole decode path is deriving Boost PSI from MAP.

## Display path (the critical path)

- **`auto_refresh = False` + manual `display.refresh()`** once per ~20 Hz UI
  tick. displayio's background auto-refresh would otherwise push SPI traffic
  at its own cadence, stealing time mid-update and risking tearing between a
  bar write and its palette write. Manual refresh batches every dirty region
  from one tick into one push, at a rate we chose (`UI_TICK_MS = 50`).
- **`bitmap_label.Label` (not `label.Label`) for all text**: renders each
  label into a single bitmap behind one TileGrid, so a text change dirties one
  rectangle - versus `label.Label`'s TileGrid-per-glyph, which costs more RAM
  and more dirty regions. The trade-off (bitmap_label re-rasterizes the whole
  string on `.text` assignment) is neutralized by the next point.
- **Change-gating on raw ints before any `.text` / `.color` / palette / bitmap
  write.** Assigning a displayio palette entry or label text marks its region
  dirty *even if the value is identical*, and `bitmap_label.text` re-rasterizes
  unconditionally - so every widget caches the last raw int (or px position /
  color int) it painted and does nothing unless that changed. A cruising dash
  with steady values costs near-zero SPI traffic and zero allocation.
- **Build once, toggle `.hidden`**: every page's widgets are constructed at
  startup into persistent Groups; page switches flip `.hidden` flags and reset
  the shared-label caches. Zero widget construction after startup - important
  because widget construction is exactly the kind of many-small-object
  allocation that fragments a MicroPython heap.
- **Bars draw with `bitmaptools.fill_region`** (native C rectangle fill), only
  on change, and write each pixel exactly once per repaint (background sides +
  fill middle) so there's no clear-then-redraw flash. Hold markers that move
  independently of the fill (AFR LO/HI, battery average) are their own tiny
  TileGrids - repositioning one repaints a 3 px strip, not the whole bar, and
  the bar's own redraws can't blink them.
- **Shift-light bulbs**: circles are drawn once at startup (bitmaptools only
  outlines circles, so a manual O(r²) fill runs at init); runtime updates only
  assign per-bulb palette entries, and each bulb's lit color is a precomputed
  constant (the gradient at *that bulb's* RPM), so lighting the bar is seven
  cached int compares.
- **SPI at 24 MHz** (`TFT_BAUDRATE`), the fastest documented-stable clock for
  ILI9341 on this board; set explicitly rather than trusting defaults.

## Memory / GC discipline

- **Steady-state allocation ≈ 0**: no f-strings/concatenation/tuple-building
  in the hot path. What still allocates, deliberately: a formatted string when
  a displayed value *changes* (a few per second while driving), one CSV row
  string per 100 ms *while logging*, one small dict per physical screen tap
  (the TSC2007 driver's API), and `struct.unpack`'s result tuple per CAN frame.
  All short-lived and small.
- **Everything reusable is preallocated at startup**; all imports are at the
  top of each file (lazy imports would stall the loop mid-drive).
- **One `gc.collect()` after startup** compacts the construction garbage so
  the loop starts with a clean heap. **Periodic collection is not needed**:
  with near-zero steady allocation, collections are rare and, on a mostly-empty
  heap, take low single-digit milliseconds - watch `mem_free` with
  `DEBUG = True` to verify on your own build (it should sawtooth slowly and
  shallowly, or barely move).

## Measured rates (CircuitPython 10.2.1, Feather M4 CAN, `DEBUG = True`)

| Thing | Measured / configured |
|---|---|
| Main loop | **~830 loops/s** bench (no CAN); **~640-820 loops/s** on a live bus (~53 frames/s) |
| Worst loop pass | **5-10 ms** typical peak (a render tick); **~125-177 ms** occasional GC spike on a live bus with DEBUG on (the ~53 canio message allocations/s churn the heap; a spike costs 2-3 of the 20 Hz frames a few times a minute - imperceptible, and lower with DEBUG's own print allocations off) |
| Display refresh | fixed 20 Hz (`UI_TICK_MS = 50`) |
| Touch poll | 50 Hz (`TOUCH_POLL_MS = 20`) |
| CAN decode capacity | ≥ 13,000 frames/s ceiling (16/pass × 830 passes/s) vs ~40-200/s actually broadcast |
| Heap | ~24 KB free after startup; sawtooths ~9-24 KB with DEBUG's own print allocations, GC pauses absorbed inside the 11 ms worst case |
| Datalog | 10 Hz rows, `flush()` per row (SD flush is the loop's one intentional stall, a few ms, engine-running only - the price of crash-proof logs) |

## Known trade-offs

- `bitmap_label` re-rasterizes on text change - fine at "a few changes/sec",
  wrong for text that changes every tick (nothing here does).
- The datalog `flush()`-per-row briefly stalls the loop while logging; accepted
  so a key-off never loses more than one row.
- Peaks/low/average survive stale data on purpose and reset only at power-off.
