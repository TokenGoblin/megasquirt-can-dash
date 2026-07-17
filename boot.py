# Intentionally does nothing.
#
# An earlier version of this file called storage.remount("/", readonly=False)
# so code.py could write log files directly to the internal CIRCUITPY flash.
# In practice supervisor.runtime.usb_connected read False here even while
# plugged into a PC (boot.py runs very early, before USB enumeration
# finishes), so it kept flipping the drive read-only to the host - the
# repeated "media is write protected" lockouts during development. The
# datalogger now writes to a physical SD card (mounted from code.py, at
# "/sd") instead, which needs no special boot.py permission dance at all -
# so this file is kept empty rather than removed, as a placeholder/reminder
# not to reintroduce that pattern without a physical jumper-pin check (the
# actually-reliable way to detect "no PC attached", per CircuitPython's own
# datalogger guides).
