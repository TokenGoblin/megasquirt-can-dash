# Third-Party Notices

Dependency and licensing inventory for `megasquirt-can-dash`, produced as part of
the audit recorded in `AUDIT-REPORT.md` (§12).

**This is engineering observation, not legal advice.** Anything flagged below
should be reviewed by counsel before it matters commercially.

---

## Summary

**No third-party code is vendored in this repository.** Every file in the tree is
first-party. There is no `lib/` directory, no bundled `.mpy` files, no copied
source, no lockfile, and no package manifest — verified by directory listing and
by `git log --diff-filter=A`, which shows only the twelve first-party files ever
added to history.

The project's dependencies are obtained *by the user*, from Adafruit, and copied
onto the device alongside this code. This is the cleanest possible licensing
posture and it means **no attribution obligation is currently triggered by
distributing this repository**. See "If you ever ship an image" below for the one
scenario that changes.

**No copyleft exposure.** No GPL, AGPL, LGPL, SSPL, BUSL, Elastic, Commons Clause,
or non-commercial-restricted code is present or depended upon. Every dependency is
MIT.

---

## Project license

| Item | Value | Verified |
|---|---|---|
| License | MIT | `LICENSE`, full standard text, unmodified |
| Copyright holder | `Copyright (c) 2026 TokenGoblin` | `LICENSE:3` |
| README claim | "MIT - see LICENSE" | `README.md:211-213` — consistent |
| Per-file headers | None present | Consistent — no file claims a different license |
| Package manifest | None (not a packaged project) | No conflicting metadata to drift |

Consistent throughout. The copyright holder matches the git author identity
(`TokenGoblin`) across all eight commits. MIT is compatible with every
dependency below.

---

## Runtime dependencies

None of these are distributed by this repository. Users install them per
`README.md:86-107`.

### CircuitPython core (built in — no install step)

| Module | Provided by | License |
|---|---|---|
| `board`, `digitalio`, `supervisor`, `storage`, `struct`, `time`, `os`, `gc` | CircuitPython | MIT |
| `canio` | CircuitPython | MIT |
| `displayio`, `fourwire`, `bitmaptools`, `terminalio` | CircuitPython | MIT |

CircuitPython is MIT-licensed (Copyright Adafruit Industries and contributors).
Target version per README: **10.x, developed against 10.2.1**.

> **Note on `terminalio.FONT`.** All rendered text in this dash uses
> CircuitPython's built-in terminal font, which ships inside the CircuitPython
> firmware rather than in this repository. Built-in console fonts in embedded
> runtimes are sometimes derived from separately-licensed bitmap fonts. Since the
> font is not redistributed by this project, no obligation attaches here.
> `UNVERIFIED` — I did not trace the font's upstream provenance in the
> CircuitPython source. It would only matter to someone redistributing a
> CircuitPython build, not to this repository.

### Adafruit CircuitPython libraries (user-installed from the bundle)

Per `README.md:94-103`, tested against bundle **20260710**.

| Library | Imported at | Upstream | License |
|---|---|---|---|
| `adafruit_display_text` (`bitmap_label`) | `ui.py:39` | Adafruit_CircuitPython_Display_Text | MIT |
| `adafruit_ili9341` | `ui.py:38` | Adafruit_CircuitPython_ILI9341 | MIT |
| `adafruit_tsc2007` | `touch.py:25` | Adafruit_CircuitPython_TSC2007 | MIT |
| `adafruit_sdcard` | `datalog.py:37` | Adafruit_CircuitPython_SD | MIT |
| `adafruit_bus_device` | transitive (driver dependency) | Adafruit_CircuitPython_BusDevice | MIT |

All five are first-party Adafruit projects published under the MIT License
(copyright variously Adafruit Industries, Limor Fried, Scott Shawcroft, Kattni
Rembor, and contributors), each carrying an `SPDX-License-Identifier: MIT` header
upstream.

**Confidence: `INFERRED`.** These licenses are read from the upstream project
convention, not from installed artifacts — nothing is vendored here for me to
read, and no lockfile records what any given user actually installed. **To
verify:** open each library's folder or `.mpy` header in your `CIRCUITPY/lib/`
and confirm the SPDX identifier, or read the `LICENSE` file inside the
downloaded bundle.

### Supply-chain observations

Checked per §5, independently of any CVE:

- **Unmaintained packages:** none. All five libraries are actively released
  Adafruit projects.
- **Single-maintainer packages on a critical path:** none — all are
  organisation-maintained.
- **Recently-added / low-download packages:** none.
- **Typosquat risk:** none. All names are canonical `adafruit_*` modules obtained
  from the official bundle release, not from PyPI.
- **Git-URL, fork, or non-default-registry dependencies:** none.
- **Vulnerability scan:** not applicable in any meaningful sense. No package
  manager, no lockfile, no manifest — `npm audit`/`pip-audit`/`osv-scanner` have
  nothing to consume. More to the point, the attack surface is nil: this code makes
  no network calls, opens no sockets, parses no untrusted archives or documents,
  and deserializes nothing. The only external inputs are CAN frames (fixed-width
  `struct.unpack` of exactly 8 bytes) and one optional BMP.

---

## Code of uncertain origin

Examined per §12 for lifted code, retained copyright headers, style
discontinuities, and unexplained magic numbers.

**Nothing found requiring attribution.** Specifically:

- **No retained third-party copyright headers** anywhere in the tree.
- **No style discontinuities.** All eight files share one distinctive header
  convention (`Purpose: / Talks to: / Fits in:`), one comment voice, and
  consistent naming. Nothing reads as pasted in.
- **Protocol constants** (`config.py:53-64`: `OFS_MAP`, `OFS_RPM`, `OFS_CLT`,
  `OFS_TPS`, `OFS_MAT`, `OFS_AFR1`, `OFS_BATT`) are cited to the EFI Analytics
  "Megasquirt CAN realtime data broadcast protocol" document (2016-02-17). These
  are seven small integers describing a published interoperability format —
  facts about a protocol, not creative expression, and the quantity involved is
  far below any threshold of concern. **No data table, header file, INI, or
  definition file from MegaSquirt, TunerStudio, or MegaLogViewer has been copied
  into this repository** — I checked specifically for this, since it is the
  classic way a permissive project acquires an obligation it does not know about.
- **Physical constants** (`KPA_PER_PSI = 6.894757`, `BARO_FALLBACK_KPA = 101.3`)
  are physics.
- **The CSV output shape** (`datalog.py:117-123`) targets MegaLogViewer HD's
  *documented* delimited-file loader. The module docstring (`datalog.py:18-23`)
  explicitly records the decision **not** to write the proprietary binary `.mlg`
  format. This is the right call for interoperability reasons and it also avoids
  reverse-engineering a closed format — worth noting as a licensing-adjacent
  decision made correctly.
- **Algorithms** — the tick-rollover arithmetic (`ticks.py:39-50`) follows the
  pattern Adafruit's own documentation recommends and the file says so. It is a
  handful of lines of standard modular arithmetic, functionally identical to
  MicroPython's `ticks_diff` and to CPython's `asyncio` tick handling; there is no
  meaningful expression to attribute. Everything else — the fixed-point store, the
  change-gated render, the centre-zero AFR bar, the baro capture — is
  project-specific.

---

## Assets and bundled content

| Category | Status |
|---|---|
| Fonts | None in repo. Text uses `terminalio.FONT` from the CircuitPython firmware (see note above). |
| Icons / images | None in repo, and none loaded at runtime — the boot splash was removed, so the dash reads no image files at all. |
| Sound | None (no audio hardware). |
| Sample / fixture data | None — and there are no tests, so there are no fixtures at all (see `AUDIT-REPORT.md` F-12). |
| Datasets / model weights | None. |
| Trademarks | "MegaSquirt", "Microsquirt", "TunerStudio", "MegaLogViewer", "Adafruit", "Feather", and "FeatherWing" appear throughout the README and comments, used nominatively to describe compatibility. No logos are reproduced and no endorsement is implied or claimed. Standard nominative use; worth keeping it that way if the project is ever promoted more broadly. |

---

## Proprietary or confidential fixtures

**None.** No test fixtures exist at all, so there is no risk of real customer
data, proprietary binaries, real API responses, or production dumps being present.

Note for when fixtures *are* added (`AUDIT-REPORT.md` F-12 recommends several):
a CAN capture from a real vehicle contains no personal data and is safe to commit,
but a datalog CSV records driving behaviour — speed-correlated RPM against a
timestamp — and a `.msq` tune file is often considered proprietary by its author.
Prefer synthetic frame sequences for the test suite; commit a real candump only if
it is the author's own vehicle and that is an intentional choice.

---

## If you ever ship a pre-loaded image

Today this repository distributes **source only** and bundles nothing, so no
third-party attribution obligation is triggered by cloning or forking it.

That changes the moment anyone distributes a ready-to-run artifact — a pre-imaged
microSD or CIRCUITPY card, a release `.zip` containing `lib/`, or an assembled
unit sold or given to another person. At that point the distributor is
redistributing MIT-licensed Adafruit code, and MIT requires the copyright notice
and permission text to travel with it.

Satisfying that is easy and worth writing down now:

1. Include each library's upstream `LICENSE` file in the distributed `lib/`
   folder (the Adafruit bundle already ships these — just do not strip them).
2. Include CircuitPython's own license with the firmware.
3. Keep this file in the distribution as the aggregated notice.

No changes are required today. This section exists so the obligation is not
discovered later by someone shipping a card to a forum member.

---

## AI-generated code disclosure

The inferred `compliance` field is "none" and no organisational policy is evident
in the repository, so no disclosure is required. Noted only because §12 asks.

---

*Inventory produced 2026-08-11 against commit `e06fc43`. Re-run it if a dependency
is added, if anything is vendored into the tree, or before publishing a
pre-loaded image.*
