# ============================================================================
#  boot.py - deliberately (almost) empty
# ============================================================================
#  Purpose:   Documents WHY this dash needs nothing in boot.py - and warns
#             against the one thing you might be tempted to add here.
#  Talks to:  Nothing.
#  Fits in:   CircuitPython runs boot.py before USB enumeration, then
#             code.py. All dash startup lives in code.py and its modules.
#
#  Do NOT add storage.remount("/", readonly=False) here to log onto the
#  internal CIRCUITPY flash. That pattern (a) makes the drive read-only to
#  your PC, turning every code tweak into a safe-mode dance, and (b) its
#  usual escape hatch - keying the remount off supervisor.runtime.usb_connected
#  - reads unreliably this early in boot (USB hasn't enumerated yet), which
#  is exactly how this project once locked itself out ("media is write
#  protected") during development. If you ever truly need it, gate it on a
#  physical jumper pin instead, per CircuitPython's own datalogger guides.
#
#  Datalogging instead writes to the FeatherWing's microSD slot, mounted at
#  /sd by datalog.py at runtime - no boot-time storage games required, and
#  the CIRCUITPY drive stays writable from your PC at all times.
# ============================================================================
