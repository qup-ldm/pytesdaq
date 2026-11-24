# PCS102 remote SQUID reset

Lets the Linux DAQ (pytesdaq) reset the SQUIDs through the PCS102DA program
running on the Windows PC. The Windows PC keeps the PCS102 USB connection.
A small server on it (`pcs102_reset_server.py`) clicks the program's
buttons when the DAQ asks:

- **All channels:** turns MASTER on if it is off, then clicks RESET.
- **One channel (1-4):** turns MASTER off if it is on, selects the Ch N
  radio button, then clicks RESET.

MASTER is on when its text is red. If the server clicks MASTER and its colour
doesn't change as expected, it clicks MASTER again to put it back and
refuses the reset.

## 1. Windows PC: install

1. Install Python 3 from python.org. Tick **"Add python.exe to PATH"**.
2. In a command prompt, run `pip install pywinauto`.
3. Copy `pcs102_reset_server.py` into a local folder you can write to, e.g.
   `C:\Users\<you>\pcs102_remote\`. Avoid OneDrive folders, network drives
   and `Program Files`.
4. With PCS102DA open, run `python pcs102_reset_server.py check` once. This
   creates `pcs102_reset_config.json` next to the script.

## 2. Windows PC: set the click positions

Positions are in pixels from the top-left of the PCS102DA window's content.
Moving the window is fine. If the window is resized or the display scaling
changes, measure them again.

1. Run `python pcs102_reset_server.py locate`. Hover over each item below and
   press Enter. It prints `{"x": .., "y": ..}` and the pixel colour.
   - RESET button
   - Centre of the MASTER button
   - A letter of the MASTER text while it is red (note the printed `rgb`)
   - The top-left and bottom-right corners of the MASTER text
   - Each Ch 1-4 radio button
2. Edit `pcs102_reset_config.json`. Every entry under `"channels"`
   (`"all"`, `"1"`-`"4"`) is a list of steps:
   - **First step:** `ensure_color` is the area around the MASTER text
     (x, y = top-left corner; w, h = size) and the red `rgb` value. `"click"`
     is the MASTER button position. Entries `"1"`-`"4"` also have
     `"present": false`, meaning MASTER must be off.
   - **Middle step (`"1"`-`"4"` only):** the Ch N radio button position.
   - **Last step:** the RESET position.

## 3. Windows PC: test

| Command | Does |
|---|---|
| `python pcs102_reset_server.py check` | Reads the MASTER colour without clicking, and says whether MASTER would be clicked. Add `2` to check the channel 2 entry. |
| `python pcs102_reset_server.py test` | Real reset of all channels after 3 seconds. |
| `python pcs102_reset_server.py test 2` | Real reset of channel 2 only after 3 seconds. |
| `python pcs102_reset_server.py serve` | Runs the server. This is what runs normally. |

## 4. Windows PC: start the server automatically at login

1. In a command prompt, run `where pythonw` and note the full path, e.g.
   `C:\Users\<you>\AppData\Local\Programs\Python\Python312\pythonw.exe`.
2. Press **Win+R**, type `shell:startup`, and press Enter. File Explorer opens
   your Startup folder.
3. In that folder, right-click → **New → Shortcut**. For the location, enter
   the following on one line, with quotes:
   ```
   "<pythonw path>" "<folder>\pcs102_reset_server.py" serve
   ```
   Name it `PCS102 reset server`.
4. Right-click the shortcut → **Properties**. Set **Start in** to the
   script folder.
5. Double-click the shortcut to start the server now. `pythonw` runs it
   without a window, so check that it's running by looking for
   "listening on 0.0.0.0:8765" in `pcs102_reset_server.log`. To stop it, end
   the `pythonw.exe` process in Task Manager.

The server starts when that user logs in. If the PC restarts, someone has to
log in again before resets work. Logging in automatically is possible
(`netplwiz`), but check your lab's IT policy first.

Also keep the PC unlocked: the server clicks with the real mouse. In
**Settings → Accounts → Sign-in options**, set "If you've been away, when
should Windows require you to sign in again" to **Never**. In **Settings →
System → Power**, set screen and sleep to **Never**.

## 5. Network

1. **Windows PC's IP:** run `ipconfig` and note the IPv4 address on the
   adapter that is on the same network as the Linux PC. The server's
   "listening on 0.0.0.0:8765" line isn't the address: `0.0.0.0` means it
   listens on all of the PC's network connections.
2. **Linux PC's IP:** run `ip -4 addr` on the Linux PC.
3. **Firewall:** on the Windows PC, in an **admin** command prompt, allow only
   the Linux PC:
   ```
   netsh advfirewall firewall add rule name="PCS102 reset" dir=in action=allow protocol=TCP localport=8765 remoteip=<linux-ip>
   ```
   If Windows showed an "allow Python on networks?" pop-up and you
   clicked **Cancel**, it created a rule that *blocks* Python, and block rules
   override allow rules. Check **Windows Defender Firewall with Advanced
   Security → Inbound Rules** for blocking `python`/`pythonw` rules and
   disable them.
4. **Test from the Linux PC:**
   `curl http://<windows-ip>:8765/ping` should print
   `{"ok": true, "window_found": true}`.
5. **Optional token:** set `"token"` in the Windows config, and put the same
   value in `token = ...` in setup.ini.

## 6. Linux PC: configure pytesdaq

1. In `pytesdaq/config/setup.ini`:
   ```
   [pcs102_remote]
   host = <windows-ip>
   port = 8765
   token = None
   timeout = 10
   ```
2. In the repo, run `pip install .` inside the tesseract venv. The DAQ uses
   the installed copy.

## Usage

```python
from pytesdaq.instruments.control import Control
ctl = Control()
ctl.reset_squid()                          # all channels (MASTER on)
ctl.reset_squid(controller_channel=2)      # PCS102 channel 2 only (MASTER off)
ctl.reset_squid(detector_channel='TES2')   # same, via setup.ini connections
```

Each call returns True or False. With the default `raise_errors=True`, a
failed reset raises an error that includes the server's reason.

After a reset, MASTER stays in whatever state that reset needed, and the
last channel stays selected. Check both before tuning by hand. Every reset is
logged in `pcs102_reset_server.log` on the Windows PC.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot reach ..." | Check that the server is running (log file, Task Manager), the firewall rule exists, and the IP is right. Try `curl .../ping`. |
| `ping` says `window_found: false` | PCS102DA isn't open, or `window_title_re` in the config doesn't match its title. Run `inspect` to list window titles. |
| "Could not enable/disable MASTER" | The colour check didn't see the expected change, so MASTER was clicked back. Run `check` and fix the `ensure_color` area, `rgb` or `click` position. |
| Clicks the wrong place | The window was resized or the scaling changed. Measure again with `locate`. |
| Resets fail when nobody is at the PC | The screen is locked or the PC is asleep. See section 4. |
| "bad token" | The token in setup.ini doesn't match the config. |
