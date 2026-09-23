# AGENTS.md - bringing up a new Feather M4 CAN dash unit

Notes for AI coding agents (and humans) flashing this dash onto a fresh or
repurposed **Adafruit Feather M4 CAN Express** from a Windows host over USB.
This is the exact process that was used to bring up a unit on 2026-09-22;
the human-oriented version is in [README.md](README.md#circuitpython-setup).

Tested versions: **CircuitPython 10.2.1**, library bundle **10.x 20260710**.

## 1. Identify what the board is running

Plug in over USB and look at the Adafruit (VID `239A`) devices:

```powershell
Get-Volume | Where-Object DriveLetter | Select DriveLetter,FileSystemLabel
Get-CimInstance Win32_PnPEntity | Where-Object PNPDeviceID -match 'VID_239A' | Select Name,PNPDeviceID
```

| USB PID | Drive label | Meaning |
|---|---|---|
| `80CD` | none, serial port only | Arduino sketch - needs CircuitPython flashed (step 2) |
| `00CD` | `FTHRCANBOOT` | UF2 bootloader - go to step 3 |
| `80CE` | `CIRCUITPY` | CircuitPython running - check `boot_out.txt` version, go to step 4 |

A serial port with no `CIRCUITPY` drive means it is **not** CircuitPython -
don't try to talk to a REPL on it.

## 2. Enter the UF2 bootloader (no button press needed)

Do a "1200-baud touch": open the board's COM port at 1200 baud and close it.
The alternative is having the human double-tap the reset button.

```powershell
$j = Start-Job { $p = New-Object System.IO.Ports.SerialPort COM6,1200; $p.Open(); $p.DtrEnable=$false; Start-Sleep -Milliseconds 200; $p.Close(); 'touched' }
if (Wait-Job $j -Timeout 10) { Receive-Job $j } else { Stop-Job $j; 'open timed out' }
```

Replace `COM6` with the port from step 1. A `FTHRCANBOOT` drive appears within a
few seconds; its `INFO_UF2.TXT` should say `Model: Feather M4 CAN Express`.

> **Gotcha:** always wrap `System.IO.Ports.SerialPort` in `Start-Job` +
> `Wait-Job -Timeout`. Opening a port directly in the PowerShell tool
> sometimes blocks forever and eats the tool's full 120 s timeout. No Python
> or pyserial is assumed to be installed on the host.

## 3. Flash CircuitPython

```bash
curl -fsSLo cp.uf2 https://downloads.circuitpython.org/bin/feather_m4_can/en_US/adafruit-circuitpython-feather_m4_can-en_US-10.2.1.uf2
```

Copy `cp.uf2` onto `FTHRCANBOOT`. The drive disconnects (the copy may
report an error; that's normal), the board reboots, and a `CIRCUITPY` drive
appears after about 10 s. Poll for it instead of sleeping:

```powershell
$t=0; while ($t -lt 30 -and -not (Get-Volume | Where-Object FileSystemLabel -eq 'CIRCUITPY')) { Start-Sleep 1; $t++ }
```

Confirm with `CIRCUITPY\boot_out.txt` (`Adafruit CircuitPython 10.2.1 ... Feather M4 CAN with same51j19a`).
The serial port gets a **new COM number** after flashing (PID `80CE`), so look it up again.

## 4. Install libraries and code

```bash
curl -fsSLo bundle.zip https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/download/20260710/adafruit-circuitpython-bundle-10.x-mpy-20260710.zip
```

Copy **only** these from the bundle's `lib/` into `CIRCUITPY/lib/`:

- `adafruit_display_text/` (folder)
- `adafruit_bus_device/` (folder)
- `adafruit_ili9341.mpy`
- `adafruit_tsc2007.mpy`
- `adafruit_sdcard.mpy`
- `adafruit_ticks.mpy`: required by current `adafruit_display_text` even though
  nothing in this repo imports it. Without it the dash dies at boot with
  `ImportError: no module named 'adafruit_ticks'`.

Then copy the repo's `*.py` files to the root of `CIRCUITPY`: `boot.py`,
`canbus.py`, `config.py`, `datalog.py`, `statusled.py`, `ticks.py`, `touch.py`, `ui.py`, then
**`code.py` last**. Auto-reload restarts the board on every write, and copying
`code.py` last keeps it from running against a half-copied tree.

Do not copy the whole bundle, because the flash is small. Do not add
`storage.remount` to `boot.py` (see the comments in `boot.py`).

## 5. Verify over the serial console

Soft-reload and capture output (use the post-flash COM port):

```powershell
$j = Start-Job { $p = New-Object System.IO.Ports.SerialPort COM8,115200; $p.DtrEnable=$true; $p.Open(); $p.Write([char]3); Start-Sleep -Milliseconds 500; $p.Write([char]4); Start-Sleep 12; $o=$p.ReadExisting(); $p.Close(); $o }
if (Wait-Job $j -Timeout 25) { Receive-Job $j } else { Stop-Job $j; 'timed out' }
```

(`Ctrl-C` = `[char]3` stops the running program, `Ctrl-D` = `[char]4` soft-reboots into `code.py`.)

A healthy boot with no splash image or SD card prints only:

```
Splash image not found/failed to load: [Errno 2] No such file/directory: /splash.bmp
No SD card detected - datalogging disabled: no SD card
```

and no traceback. A `KeyboardInterrupt` traceback in the capture is just
your own Ctrl-C landing in the main loop, not a fault.

### Hardware probe from the REPL

To run a multi-line snippet, press Ctrl-C twice, then paste mode `[char]5`,
send the code with `\r` stripped, then `[char]4`:

```python
import board,canio,time,config
i2c=board.I2C()
while not i2c.try_lock(): pass
print('I2C:',[hex(a) for a in i2c.scan()]); i2c.unlock()
can=canio.CAN(rx=board.CAN_RX,tx=board.CAN_TX,baudrate=config.CAN_BAUD_RATE,auto_restart=True)
l=can.listen(timeout=0.1); n=0; ids=set(); t=time.monotonic()
while time.monotonic()-t<3:
    m=l.receive()
    if m: n+=1; ids.add(m.id)
print('CAN frames:',n,'ids:',sorted(ids),'state:',can.state,'tec/rec:',can.transmit_error_count,can.receive_error_count)
```

- `I2C: ['0x48']` means the TSC2007 touch controller is present, so a **V2** FeatherWing is seated.
  If 0x48 is missing, the wing is V1 (unsupported) or not seated.
- `ERROR_ACTIVE` with 0/0 error counts means the CAN peripheral is healthy.
  `0 frames` is expected on the bench with no ECU. With a Microsquirt
  broadcasting you should see IDs starting at `BASE_CAN_ID` (1512 = 0x5E8).

Send `[char]4` afterwards to put the board back into `code.py`.

### Status LED

The onboard NeoPixel (`statusled.py`, settings in `config.py`'s STATUS LED
section) is the quickest health check the human can give you:

| LED | State |
|---|---|
| Green flicker (1%) | dash frames arriving |
| Blue → purple breathing (0-5%, 4 s) | bus healthy, no frames for 150 ms |
| Yellow double flash (15%, 1.2 s cycle) over the flicker | datalog session writing to SD |
| Solid red (1%) | CAN error-passive / bus-off (overrides all) |

**On the bench with nothing on the CAN pads, a healthy unit breathes blue →
purple.** The dash never transmits, so an unconnected bus stays `ERROR_ACTIVE`
rather than going red.

To exercise every state without an ECU, run this in the REPL (paste mode as
above). It sends MegaSquirt frames in CAN **loopback** through the real
`CanBus` class and simulates the logging flag. `canio` is a built-in module
whose attributes can't be reassigned, so the snippet swaps in a small wrapper
object instead:

```python
import canio,canbus,statusled,ticks,config
class _Shim:
    Match=canio.Match; BusState=canio.BusState
    @staticmethod
    def CAN(**k): return canio.CAN(loopback=True,silent=True,**k)
canbus.canio=_Shim
bus=canbus.CanBus(); ecu=canbus.EcuData(); led=statusled.StatusLed()
frame=canio.Message(id=config.BASE_CAN_ID,data=bytes([3,72,0,0,2,188,0,0]))
def run(secs,send,ok=True,log=False):
    t=ticks.ms(); nxt=t
    while ticks.diff(ticks.ms(),t)<secs*1000:
        now=ticks.ms()
        if send and ticks.diff(now,nxt)>=0:
            bus._can.send(frame); nxt=now+20
        bus.update(ecu,now); led.update(now,bus.last_rx,ok,log)
print('LOG+TRAFFIC'); run(8,True,log=True)
print('TRAFFIC'); run(5,True)
print('IDLE'); run(6,False)
print('BUS ERROR'); run(3,False,ok=False)
print('map_x10:',ecu.value_x10[config.PARAM_MAP])   # 840 = frame decoded
```

Tell the human to watch the LED **before** you send it; it runs for about 22 s.
To try a different brightness without editing files, set
`config.LED_BRIGHTNESS=...` (or `LED_LOG_BRIGHTNESS` etc.) before
constructing `StatusLed()`. The lowest level that still lights is ~0.004.

## 6. What an agent cannot verify

You can't see the screen or the LED. Ask the human to confirm that the gauges
render, that touch paging works, and that the LED breathes on the bench; if
taps are mirrored, swap `TS_RAW_X_MIN` and `TS_RAW_X_MAX` in `config.py`. Live-data testing needs the Feather wired to a
running ECU (CANH/CANL, 120 Ω termination at each end of the bus).
