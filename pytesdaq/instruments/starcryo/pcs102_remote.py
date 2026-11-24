"""
Client for the PCS102 (STAR Cryoelectronics pcSQUID) remote reset server
running on the Windows PC (see windows/pcs102/pcs102_reset_server.py).

The Windows server clicks the GUI "Reset" button; this client only sends
HTTP requests (standard library, no extra dependency).
"""

import json
import urllib.request
import urllib.error


class PCS102Remote:
    """
    Trigger SQUID resets through the PCS102 GUI on a remote Windows PC
    """

    def __init__(self, host, port=8765, token=None, timeout=10.0,
                 verbose=True):
        self._base_url = f'http://{host}:{int(port)}'
        self._token = token if token else None
        self._timeout = float(timeout)
        self._verbose = verbose

    def _request(self, method, path):
        req = urllib.request.Request(self._base_url + path, method=method,
                                     data=b'' if method == 'POST' else None)
        if self._token:
            req.add_header('X-Token', self._token)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # server replies JSON with error description
            try:
                return json.loads(e.read().decode())
            except Exception:
                return {'ok': False, 'error': f'HTTP {e.code}'}
        except (urllib.error.URLError, OSError) as e:
            return {'ok': False,
                    'error': f'Cannot reach {self._base_url}: {e}'}

    def ping(self):
        """
        Check server is up and the PCS102 window is found

        Returns:
           bool
        """
        reply = self._request('GET', '/ping')
        ok = bool(reply.get('ok')) and bool(reply.get('window_found'))
        if self._verbose and not ok:
            print('WARNING: PCS102 remote ping failed: '
                  + str(reply.get('error', 'PCS102 window not found')))
        return ok

    def reset(self, channel='all'):
        """
        Reset SQUID (click GUI reset). channel='all' (default) resets
        all channels (PCS102 MASTER mode), otherwise a PCS102 channel
        as configured on the Windows server

        Returns:
           bool
        """
        reply = self._request('POST', f'/reset?channel={channel}')
        ok = bool(reply.get('ok'))
        if self._verbose:
            if ok:
                print(f'INFO: PCS102 reset sent for channel {channel}')
            else:
                print(f'ERROR: PCS102 reset channel {channel} failed: '
                      + str(reply.get('error')))
        return ok
