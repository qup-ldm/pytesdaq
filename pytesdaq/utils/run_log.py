"""
Run directory bookkeeping: copy the configuration files used and
write the screen output (log) into the run (group) directory.

Typical use (run_daq.py, run_iv_didv.py):

    run_log.start(mode)               # first thing in the script
    ...
    run_log.save_config_files(run_dir, [setup_file, daq_file])
    run_log.attach(run_dir)           # once the run directory exists

Log modes:
   'copy': terminal output as usual + copy in <run_dir>/run.log
   'file': output only in <run_dir>/run.log (terminal shows its path)
   'none': no log file (terminal only)

Output printed before the run directory is known is kept in memory
and written at the beginning of the log. Errors and tracebacks
(stderr) are logged too. Without start() (e.g. notebooks), attach()
does nothing; configuration files are still copied.
"""

import atexit
import io
import os
import shutil
import sys
import threading
from datetime import datetime

LOG_MODES = ('copy', 'file', 'none')

_lock = threading.RLock()
_state = {'mode': 'none', 'buffer': None, 'file': None, 'path': None,
          'stdout': None, 'stderr': None}


class _Tee(io.TextIOBase):
    """
    Replacement for sys.stdout/sys.stderr writing to the terminal
    (mode 'copy') and to the log file (or memory buffer until a
    log file is attached)
    """

    def __init__(self, terminal):
        self._terminal = terminal

    def write(self, text):
        with _lock:
            if _state['mode'] == 'copy':
                self._terminal.write(text)
            if _state['file'] is not None:
                _state['file'].write(text)
            elif _state['buffer'] is not None:
                _state['buffer'].write(text)
        return len(text)

    def flush(self):
        with _lock:
            self._terminal.flush()
            if _state['file'] is not None:
                _state['file'].flush()

    def isatty(self):
        return self._terminal.isatty()

    def fileno(self):
        return self._terminal.fileno()

    @property
    def encoding(self):
        return getattr(self._terminal, 'encoding', 'utf-8')


def _terminal_print(message):
    """
    Print to the real terminal regardless of log mode
    """
    terminal = _state['stdout'] or sys.stdout
    terminal.write(message + '\n')
    terminal.flush()


def start(mode='copy'):
    """
    Start capturing screen output (stdout and stderr)
    """
    if mode not in LOG_MODES:
        raise ValueError(f'Log mode "{mode}" not recognized, '
                         f'choose from {LOG_MODES}')
    with _lock:
        if _state['stdout'] is not None:
            stop()
        _state['mode'] = mode
        if mode == 'none':
            return
        _state['buffer'] = io.StringIO()
        _state['buffer'].write(
            f'# {datetime.now():%Y-%m-%d %H:%M:%S}  '
            f'{" ".join(sys.argv)}\n')
        _state['stdout'], _state['stderr'] = sys.stdout, sys.stderr
        sys.stdout = _Tee(_state['stdout'])
        sys.stderr = _Tee(_state['stderr'])
    if mode == 'file':
        _terminal_print('INFO: Output goes to "run.log" in the run '
                        'directory (path shown once created)')


def attach(run_dir, name='run.log'):
    """
    Write the log into run_dir/name (buffered output first).
    Calling again with another directory switches log files.

    Returns:
       str or NoneType (log file path)
    """
    with _lock:
        if _state['mode'] == 'none' or _state['stdout'] is None:
            return None
        path = os.path.join(run_dir, name)
        if path == _state['path']:
            return path
        os.makedirs(run_dir, exist_ok=True)
        new_file = open(path, 'a', buffering=1)
        if _state['file'] is not None:
            _state['file'].write(f'# log continued in {path}\n')
            _state['file'].close()
        elif _state['buffer'] is not None:
            new_file.write(_state['buffer'].getvalue())
            _state['buffer'] = None
        _state['file'], _state['path'] = new_file, path
    if _state['mode'] == 'file':
        _terminal_print(f'INFO: Logging to {path}')
    else:
        print(f'INFO: Log file: {path}')
    return path


def stop():
    """
    Restore terminal output and close the log file
    """
    with _lock:
        if _state['stdout'] is not None:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = _state['stdout'], _state['stderr']
        if _state['file'] is not None:
            _state['file'].close()
        _state.update({'buffer': None, 'file': None, 'path': None,
                       'stdout': None, 'stderr': None})


atexit.register(stop)


def save_config_files(run_dir, files, subdir='config'):
    """
    Copy configuration files into run_dir/subdir
    (missing / None entries skipped)

    Returns:
       list of copied file paths
    """
    output_dir = os.path.join(run_dir, subdir)
    copied = list()
    for file_path in files:
        if not file_path or not os.path.isfile(file_path):
            continue
        os.makedirs(output_dir, exist_ok=True)
        destination = os.path.join(output_dir,
                                   os.path.basename(file_path))
        shutil.copy2(file_path, destination)
        copied.append(destination)
    if copied:
        print(f'INFO: Configuration files copied to {output_dir}: '
              + ', '.join(os.path.basename(f) for f in copied))
    return copied
