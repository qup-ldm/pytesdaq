"""
PCS102 (STAR Cryoelectronics pcSQUID) remote reset server  --  WINDOWS ONLY

Small HTTP server that clicks the "Reset" button(s) of the running
pcSQUID PCS102/PCS102DA program, so the Linux DAQ (pytesdaq) can trigger
SQUID resets over the network while the GUI keeps ownership of the USB.

Requirements (Windows PC):
    python 3.8+
    pip install pywinauto

Files (same folder):
    pcs102_reset_server.py
    pcs102_reset_config.json   (created with defaults on first run)

Usage:
    python pcs102_reset_server.py inspect     # list GUI controls of the PCS102 window
    python pcs102_reset_server.py locate      # record button positions with the mouse
    python pcs102_reset_server.py check       # test the MASTER color guard only (no click)
    python pcs102_reset_server.py test        # perform the "all" reset locally
    python pcs102_reset_server.py test 1      # perform the channel 1 reset locally
    python pcs102_reset_server.py serve       # run the HTTP server (default)

HTTP API:
    GET  /ping                  -> {"ok": true, "window_found": true/false}
    POST /reset?channel=all     -> {"ok": true, "channel": "all"}
    POST /reset?channel=1       -> {"ok": true, "channel": 1}
    (header "X-Token: <token>" required if "token" set in config)

Config "channels": per channel, a list of steps executed in order, e.g.
    "1": [ {"x": 120, "y": 340} ]                                   click at
                                                                    window client coords
    "2": [ {"x": 60, "y": 20, "delay": 0.3}, {"x": 120, "y": 340} ] select tab, then reset
    "3": [ {"control": {"title": "Reset", "class_name": "Button"}} ] click a named control
    "4": [ {"x": 64, "y": 245, "delay": 0.3}, {"key": "r"} ]       select Ch 4, press R
Channel names are free strings, e.g. an "all" entry is allowed.

PCS102DA layout: one RESET button. With MASTER on (red text) it resets
ALL channels; with MASTER off it acts on the selected Ch 1-4 box.
Default "all" entry: turn MASTER on if not red, then click RESET.
Color steps (box in client coords, "locate" prints colors):
  {"expect_color": {"x","y","w","h","rgb":[r,g,b],"tol"}, "error": "..."}
      fail unless a pixel in the box matches
  {"ensure_color": {...box...}, "click": {"x","y"}, "delay": 0.5, "error": "..."}
      if no match, click toggle and re-check; if still no match, click
      again (restore) and fail. Add "present": false to ensure the color
      is ABSENT instead (single channel resets: MASTER must be off)
Default channels "1"-"4": MASTER off, click Ch N radio button, RESET.
Coordinates are relative to the top-left of the window CLIENT area, as printed
by "locate", so they survive moving the window (not resizing/re-layout).
"""

import ctypes
import ctypes.wintypes
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, 'pcs102_reset_config.json')
LOG_FILE = os.path.join(HERE, 'pcs102_reset_server.log')

# Default channel steps for the PCS102DA window layout: click the "Ch N"
# select box, then RESET. Coordinates are ESTIMATED from a screenshot
# at 100% display scaling -> verify/redo them with "locate"!
DEFAULT_CONFIG = {
    # regex matched against top-level window titles; check with "inspect"
    'window_title_re': '.*PCS102.*SQUID.*',
    # pywinauto backend: "win32" (LabWindows/CVI apps) or "uia"
    'backend': 'win32',
    # "mouse": real mouse click (needs unlocked, visible desktop)
    # "message": posted window messages (no mouse/focus, may be ignored by app)
    'click_method': 'mouse',
    'host': '0.0.0.0',
    'port': 8765,
    'token': '',
    'channels': {
        # MASTER on (red text) -> RESET resets all channels.
        # Turn MASTER on if needed (click it if not red), then RESET
        'all': [
            {'ensure_color': {'x': 207, 'y': 87, 'w': 60, 'h': 12,
                              'rgb': [255, 0, 0], 'tol': 80},
             'click': {'x': 237, 'y': 93},
             'delay': 0.5,
             'error': 'Could not enable MASTER: refusing reset'},
            {'x': 237, 'y': 37},
        ],
        # single channel: turn MASTER OFF if red, select Ch N radio
        # button, then RESET
        **{str(ch): [
            {'ensure_color': {'x': 207, 'y': 87, 'w': 60, 'h': 12,
                              'rgb': [255, 0, 0], 'tol': 80},
             'present': False,
             'click': {'x': 237, 'y': 93},
             'delay': 0.5,
             'error': 'Could not disable MASTER: refusing reset'},
            {'x': 64, 'y': y, 'delay': 0.3},
            {'x': 237, 'y': 37},
        ] for ch, y in ((1, 146), (2, 179), (3, 212), (4, 245))},
    },
}

user32 = ctypes.windll.user32
_lock = threading.Lock()   # one GUI action at a time


# ----------------------------------------------------------------------
# config / logging
# ----------------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        print(f'Created default config: {CONFIG_FILE} -> edit it!')
    with open(CONFIG_FILE) as f:
        config = dict(DEFAULT_CONFIG)
        config.update(json.load(f))
    return config


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.FileHandler(LOG_FILE),
                  logging.StreamHandler(sys.stdout)])


# ----------------------------------------------------------------------
# GUI helpers
# ----------------------------------------------------------------------

def find_window(config, spec=False):
    """
    Return pywinauto wrapper of the PCS102 main window
    (raises if not found). spec=True also returns the
    WindowSpecification (needed for child_window lookups)
    """
    from pywinauto import Desktop
    desktop = Desktop(backend=config['backend'])
    win_spec = desktop.window(title_re=config['window_title_re'],
                              top_level_only=True, visible_only=False)
    win = win_spec.wrapper_object()
    if spec:
        return win, win_spec
    return win


def client_to_screen(hwnd, x, y):
    pt = ctypes.wintypes.POINT(int(x), int(y))
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def screen_to_client(hwnd, x, y):
    pt = ctypes.wintypes.POINT(int(x), int(y))
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def cursor_pos():
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def screen_pixel(sx, sy):
    """
    (r, g, b) of screen pixel
    """
    b, g, r = grab_region(int(sx), int(sy), 1, 1)[:3]
    return r, g, b


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', ctypes.wintypes.DWORD),
                ('biWidth', ctypes.wintypes.LONG),
                ('biHeight', ctypes.wintypes.LONG),
                ('biPlanes', ctypes.wintypes.WORD),
                ('biBitCount', ctypes.wintypes.WORD),
                ('biCompression', ctypes.wintypes.DWORD),
                ('biSizeImage', ctypes.wintypes.DWORD),
                ('biXPelsPerMeter', ctypes.wintypes.LONG),
                ('biYPelsPerMeter', ctypes.wintypes.LONG),
                ('biClrUsed', ctypes.wintypes.DWORD),
                ('biClrImportant', ctypes.wintypes.DWORD)]


def grab_region(sx, sy, w, h):
    """
    Copy a screen region in ONE operation (GetPixel per pixel
    is very slow with desktop composition)

    Returns:
       bytes, BGRA top-down rows (4*w*h)
    """
    gdi32 = ctypes.windll.gdi32
    HANDLE = ctypes.c_void_p
    user32.GetDC.restype = HANDLE
    user32.GetDC.argtypes = [HANDLE]
    user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
    gdi32.CreateCompatibleDC.restype = HANDLE
    gdi32.CreateCompatibleDC.argtypes = [HANDLE]
    gdi32.CreateCompatibleBitmap.restype = HANDLE
    gdi32.CreateCompatibleBitmap.argtypes = [HANDLE, ctypes.c_int,
                                             ctypes.c_int]
    gdi32.SelectObject.restype = HANDLE
    gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
    gdi32.BitBlt.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, HANDLE,
                             ctypes.c_int, ctypes.c_int,
                             ctypes.wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [HANDLE, HANDLE, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [HANDLE]
    gdi32.DeleteDC.argtypes = [HANDLE]

    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, hbm)
    try:
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, sx, sy,
                     0x00CC0020)  # SRCCOPY
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h      # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        buf = ctypes.create_string_buffer(4 * w * h)
        gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0)
        return buf.raw
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def region_has_color(hwnd, box):
    """
    Check if any pixel in client-coordinate box {x, y, w, h}
    is within "tol" (per RGB component) of "rgb"

    Returns:
       (bool, closest_rgb, (x, y) of closest pixel)
    """
    target = box['rgb']
    tol = int(box.get('tol', 60))
    w, h = max(1, int(box.get('w', 1))), max(1, int(box.get('h', 1)))
    sx, sy = client_to_screen(hwnd, box['x'], box['y'])
    data = grab_region(sx, sy, w, h)

    best = (None, None, 10**9)
    for i in range(w * h):
        b, g, r = data[4*i], data[4*i + 1], data[4*i + 2]
        dist = max(abs(r - target[0]), abs(g - target[1]),
                   abs(b - target[2]))
        if dist < best[2]:
            best = ((r, g, b),
                    (box['x'] + i % w, box['y'] + i // w), dist)
    return best[2] <= tol, best[0], best[1]


def click_step(win, win_spec, step, method):
    """
    Execute one click step
    """
    from pywinauto import mouse

    if 'ensure_color' in step:
        # toggle guard: if color missing (e.g. MASTER not red),
        # click the toggle once, then re-check. If still missing,
        # click again to undo (detection likely wrong) and fail.
        # "present": false -> ensure color ABSENT (e.g. MASTER off)
        box = step['ensure_color']
        want = bool(step.get('present', True))
        found, rgb, xy = region_has_color(win.handle, box)
        if found == want:
            return
        toggle = dict(step['click'])
        logging.info('Color %s %s (closest %s): clicking toggle at '
                     '(%s, %s)', box['rgb'],
                     'missing' if want else 'present', rgb,
                     toggle['x'], toggle['y'])
        click_step(win, win_spec, toggle, method)
        time.sleep(float(step.get('delay', 0.5)))
        found, rgb, xy = region_has_color(win.handle, box)
        if found == want:
            return
        click_step(win, win_spec, toggle, method)   # undo
        msg = step.get('error', 'Toggle did not reach expected color state')
        raise RuntimeError(f'{msg} [rgb={box["rgb"]} wanted '
                           f'{"present" if want else "absent"}, closest '
                           f'found rgb={rgb} at {xy}; toggle clicked '
                           f'twice to restore state]')

    if 'expect_color' in step:
        # guard: fail unless some pixel in the box has the expected
        # color (e.g. MASTER text red). Needs the window visible.
        box = step['expect_color']
        ok, rgb, xy = region_has_color(win.handle, box)
        if not ok:
            msg = step.get('error', 'Expected color not found')
            raise RuntimeError(f'{msg} [wanted rgb={box["rgb"]}, closest '
                               f'found rgb={rgb} at {xy}]')
        return

    if 'control' in step:
        ctrl = win_spec.child_window(**step['control']).wrapper_object()
        if method == 'mouse':
            ctrl.click_input()
        else:
            ctrl.click()   # BM_CLICK / messages, no mouse movement

    elif 'x' in step and 'y' in step:
        x, y = int(step['x']), int(step['y'])
        if method == 'mouse':
            sx, sy = client_to_screen(win.handle, x, y)
            mouse.click(button='left', coords=(sx, sy))
        else:
            # target the (child) window under the point
            sx, sy = client_to_screen(win.handle, x, y)
            child = user32.WindowFromPoint(ctypes.wintypes.POINT(sx, sy))
            hwnd = child if child else win.handle
            cx, cy = screen_to_client(hwnd, sx, sy)
            lparam = (cy << 16) | (cx & 0xFFFF)
            user32.PostMessageW(hwnd, 0x0201, 0x0001, lparam)  # LBUTTONDOWN
            time.sleep(0.05)
            user32.PostMessageW(hwnd, 0x0202, 0x0000, lparam)  # LBUTTONUP
    elif 'key' in step:
        # keyboard shortcut, e.g. {"key": "r"} (PCS102 RESET = "R")
        key = str(step['key'])
        if method == 'mouse':
            win.type_keys(key)   # real keystrokes to focused window
        else:
            vk = ord(key.upper())
            user32.PostMessageW(win.handle, 0x0100, vk, 0)        # KEYDOWN
            user32.PostMessageW(win.handle, 0x0102, ord(key), 0)  # CHAR
            user32.PostMessageW(win.handle, 0x0101, vk, 0xC0000001)  # KEYUP
    else:
        raise ValueError(f'Invalid step {step}: '
                         f'need "control", "x"/"y" or "key"')

    time.sleep(float(step.get('delay', 0.1)))


def do_reset(config, channel):
    """
    Perform the configured click sequence for a channel
    """
    steps = config['channels'].get(str(channel))
    if not steps:
        raise ValueError(f'Channel {channel} not configured '
                         f'(configured: {list(config["channels"])})')

    method = config['click_method']

    with _lock:
        win, win_spec = find_window(config, spec=True)

        prev_pos = cursor_pos()
        prev_fg = user32.GetForegroundWindow()

        if method == 'mouse':
            if win.is_minimized():
                win.restore()
            win.set_focus()
            time.sleep(0.2)

        try:
            for step in steps:
                click_step(win, win_spec, step, method)
        finally:
            if method == 'mouse':
                # put mouse and focus back where the user had them
                user32.SetCursorPos(*prev_pos)
                if prev_fg:
                    user32.SetForegroundWindow(prev_fg)


# ----------------------------------------------------------------------
# HTTP server
# ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    config = None

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        token = self.config.get('token')
        if token and self.headers.get('X-Token') != token:
            self._reply(403, {'ok': False, 'error': 'bad token'})
            return False
        return True

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != '/ping':
            return self._reply(404, {'ok': False, 'error': 'not found'})
        if not self._authorized():
            return
        try:
            find_window(self.config)
            found = True
        except Exception:
            found = False
        self._reply(200, {'ok': True, 'window_found': found})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != '/reset':
            return self._reply(404, {'ok': False, 'error': 'not found'})
        if not self._authorized():
            return

        query = parse_qs(url.query)
        channel = query.get('channel', [None])[0]
        if channel is None:
            return self._reply(400, {'ok': False,
                                     'error': 'missing channel'})
        try:
            do_reset(self.config, channel)
        except Exception as e:
            logging.exception('Reset channel %s FAILED (from %s)',
                              channel, self.client_address[0])
            return self._reply(500, {'ok': False, 'channel': channel,
                                     'error': f'{type(e).__name__}: {e}'})

        logging.info('Reset channel %s (from %s)',
                     channel, self.client_address[0])
        self._reply(200, {'ok': True, 'channel': channel})

    def log_message(self, fmt, *args):
        pass   # quiet default access log; we log resets ourselves


def serve(config):
    Handler.config = config
    server = HTTPServer((config['host'], int(config['port'])), Handler)
    logging.info('PCS102 reset server listening on %s:%s',
                 config['host'], config['port'])
    server.serve_forever()


# ----------------------------------------------------------------------
# setup helpers
# ----------------------------------------------------------------------

def inspect(config):
    from pywinauto import Desktop
    print('Top-level windows:')
    for w in Desktop(backend=config['backend']).windows():
        if w.window_text():
            print(f'   "{w.window_text()}"')
    print(f'\nControls of window matching "{config["window_title_re"]}":\n')
    win, win_spec = find_window(config, spec=True)
    print(f'Window: "{win.window_text()}"  class: {win.class_name()}\n')
    win_spec.print_control_identifiers()


def locate(config):
    win = find_window(config)
    print(f'Window: "{win.window_text()}"')
    print('Hover the mouse over a button and press Enter '
          '(Ctrl+C to quit).')
    while True:
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            break
        sx, sy = cursor_pos()
        x, y = screen_to_client(win.handle, sx, sy)
        print(f'   {{"x": {x}, "y": {y}}}    color rgb={screen_pixel(sx, sy)}')


def main():
    config = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'serve'

    if cmd == 'inspect':
        inspect(config)
    elif cmd == 'locate':
        locate(config)
    elif cmd == 'test':
        channel = sys.argv[2] if len(sys.argv) > 2 else 'all'
        print(f'Resetting channel {channel} in 3 s...')
        time.sleep(3)
        do_reset(config, channel)
        print('Done')
    elif cmd == 'check':
        # run only the color guard steps (no clicks)
        channel = sys.argv[2] if len(sys.argv) > 2 else 'all'
        win = find_window(config)
        if win.is_minimized():
            win.restore()
        win.set_focus()
        time.sleep(0.3)
        for step in config['channels'].get(channel, []):
            box = step.get('expect_color', step.get('ensure_color'))
            if box:
                want = bool(step.get('present', True))
                found, rgb, xy = region_has_color(win.handle, box)
                print(f'box {box}  (want color '
                      f'{"present" if want else "absent"})\n'
                      f'  -> color {"FOUND" if found else "NOT found"}: '
                      f'closest rgb={rgb} at {xy}  =>  '
                      f'{"OK, no toggle click needed" if found == want else "toggle would be clicked"}')
    elif cmd == 'serve':
        setup_logging()
        serve(config)
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
