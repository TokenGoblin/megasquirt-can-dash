# Contributing

Thanks for looking. This project exists so MegaSquirt owners have an
inexpensive open-source alternative to a commercial CAN dash, and it gets better mostly through people running it on hardware the
author doesn't own. See
[Contributing in the README](README.md#contributing) for what's most valuable
right now; this file is the practical side.

## The fastest useful contribution

You do not need to write code. The single biggest open gap is that the CAN
decode table has only ever been validated against **one** Microsquirt. A
`candump` capture of IDs 1512–1515 from a running engine, posted alongside the
same values as TunerStudio shows them, would let that become a committed test
fixture — and would settle several findings the audit had to leave open. Open an
issue with the capture attached.

Reports that a build worked unchanged on a different MegaSquirt variant are also
genuinely useful. A project with one known install cannot tell "portable" from
"lucky".

## Development setup

There isn't one, by design. No package manager, no virtualenv, no dependencies:

```
git clone https://github.com/TokenGoblin/megasquirt-can-dash.git
cd megasquirt-can-dash
python tests/run_tests.py          # -v for per-test names
```

Any Python 3.11+ works. `tests/stubs.py` installs inert stand-ins for the
CircuitPython-only modules (`board`, `canio`, `displayio`, ...), so the real dash
code runs on a desktop against real `struct`-packed CAN frames.

Keeping the suite dependency-free is a deliberate constraint, not an oversight —
please don't add pytest or a requirements file.

## What the tests can and cannot catch

The suite covers pure logic: fixed-point formatting, tick rollover, CAN decode,
the barometric lock, boost conversion, bar geometry, log-file numbering, config
validation. Green tests mean the arithmetic is right.

They cannot see the entire category of failure that matters most here —
CircuitPython API differences, RAM exhaustion, pixel collisions, and how any of
it feels at 20 Hz on a 2.4" panel in a moving car. Both bugs found during the
last hardware bring-up were `MemoryError`s that a fully green desktop suite had
no way to detect.

**If your change touches the display path, the main loop, or allocates anything
at runtime, please run it on a board** and work through the relevant stages of
[BRINGUP.md](BRINGUP.md). Say in the PR which stages you ran and which you
didn't — "tests pass, not run on hardware" is an honest and acceptable answer,
it just tells the maintainer what still needs checking.

## Conventions

- **`config.py` is the only home for tunables.** CAN IDs, byte offsets, colors,
  thresholds, layout constants, timings. No magic numbers in the other modules —
  that separation is what makes this forkable, which is the whole point.
- **Nothing blocks.** The main loop is cooperative and every subsystem gets
  polled on a deadline; no `time.sleep()`, no busy-waits, no long-running work in
  a render path. Read [PERFORMANCE.md](PERFORMANCE.md) before changing anything
  in that area.
- **Watch allocations.** There's roughly 16.5 KB of heap free after startup. New
  full-screen bitmaps and per-tick object churn are the two things that have
  actually broken this project. Run with `DEBUG = True` and compare the free-mem
  line before and after your change.
- **Fail soft, except for the display.** Every subsystem here is optional except
  the screen — a yanked SD card or a glitching touch chip should cost you that
  subsystem and nothing else. Don't add an exception path that can take the
  gauges down.
- Comments explain *why*, not *what*. CircuitPython discards docstrings, so
  documentation here costs zero RAM — there is no reason to be terse.

## Pull requests

- Branch off `main`.
- Logic changes should come with a test.
- Run `python tests/run_tests.py` before pushing; CI runs the same suite on
  Python 3.11/3.12/3.13.
- Describe what you ran it on. Bench? Car? Neither?

## Reporting bugs

Use the issue templates — they ask for the things that are almost always needed:
your MegaSquirt variant and firmware, CircuitPython version, what the screen
shows, and the USB serial console output. That last one matters most: every
caught fault prints its exception there, and it's usually the difference between
a diagnosis and a guess.

## License

Contributions are accepted under the [MIT License](LICENSE), same as the rest of
the project.
