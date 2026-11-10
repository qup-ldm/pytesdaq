"""
Generic test of a TES bias / dIdV signal generator source through
pytesdaq Control, using the same calls as the IV/dIdV sequencer.
Works with any controller that Control supports as both
"tes_controller" and "signal_generator" (e.g. lakeshoreM81, rigolDG852).

Usage:
   python test_new_source.py [--controller lakeshoreM81|rigolDG852]
                             [--setup setup.ini] [--channel TES1]
                             [--bias 10] [--didv 4] [--freq 80]
                             [--with-laser]

  --controller  override tes_controller and signal_generator from
                setup file (default: use setup file as is)
  --bias        TES bias [uA] used for the test (keep small!)
  --didv        dIdV amplitude [mVpp] (converted with signal_gen_resistance)
  --with-laser  also connect laser signal generator (skipped by default)

The source state (TES bias, signal generator on/off, output on/off)
is restored at the end.
"""

import os
import sys
import argparse
import tempfile

# use the development tree (not an older installed pytesdaq)
DEV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, DEV_PATH)

import pytesdaq
from pytesdaq.instruments.control import Control

TOLERANCE = 0.02  # 2%

results = list()


def check(name, measured, expected, tolerance=TOLERANCE):
    """ Compare values and print PASS/FAIL """
    if expected == 0:
        ok = abs(measured) < 1e-12
    else:
        ok = abs(measured-expected)/abs(expected) <= tolerance
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {name}: measured = {measured:.6g}, expected = {expected:.6g}')
    results.append((name, ok))


def check_equal(name, measured, expected):
    ok = measured == expected
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {name}: measured = {measured}, expected = {expected}')
    results.append((name, ok))


def output_state(inst):
    """ Output on/off as 'on'/'off' (drivers return bool or str) """
    val = inst.get_generator_onoff(source=1)
    if isinstance(val, str):
        return val.lower()
    return 'on' if val else 'off'


def make_setup_file(setup, controller=None, with_laser=False):
    """
    Temporary copy of setup file with controller override and
    (optionally) laser signal generator disabled
    """
    if controller is None and with_laser:
        return setup

    lines = list()
    with open(setup) as f:
        for line in f:
            key = line.split('=')[0].strip()
            if controller is not None and key in ('tes_controller', 'signal_generator'):
                line = key + ' = ' + controller + '\n'
            elif not with_laser and key == 'laser_signal_generator':
                line = '#' + line
            lines.append(line)

    tmp = tempfile.NamedTemporaryFile('w', suffix='.ini', delete=False)
    tmp.writelines(lines)
    tmp.close()
    return tmp.name


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Test TES bias / signal generator source')
    parser.add_argument('--controller', type=str, default=None,
                        help='tes_controller/signal_generator override')
    parser.add_argument('--setup', type=str,
                        default=os.path.join(DEV_PATH, 'pytesdaq', 'config', 'setup.ini'))
    parser.add_argument('--channel', type=str, default='TES1',
                        help='detector channel (setup.ini)')
    parser.add_argument('--bias', type=float, default=10.0, help='TES bias [uA]')
    parser.add_argument('--didv', type=float, default=4.0, help='dIdV amplitude [mVpp]')
    parser.add_argument('--freq', type=float, default=80.0, help='dIdV frequency [Hz]')
    parser.add_argument('--with-laser', action='store_true',
                        help='also connect laser signal generator')
    args = parser.parse_args()

    print(f'pytesdaq used: {pytesdaq.__file__}')

    setup_file = make_setup_file(args.setup, controller=args.controller,
                                 with_laser=args.with_laser)
    chan = args.channel

    # ------------------------
    # connect
    # ------------------------
    print('\n1) Connect')
    control = Control(setup_file=setup_file, verbose=True)
    inst = control.get_tes_controller()          # TES bias
    sg = control.get_signal_gen_controller()     # dIdV (may be a separate
                                                 # object, same device)

    print(f'  TES controller   = {control.tes_controller_name}')
    print(f'  Signal generator = {control.signal_generator_name}')
    if inst is None or sg is None:
        print('ERROR: TES controller and signal generator required!')
        sys.exit(1)
    check_equal('TES/SG controllers common', control.is_tes_signal_gen_inst_common(), True)

    r_bias = float(control.get_tes_bias_resistance(detector_channel=chan))
    r_sg = float(control.get_signal_gen_resistance(detector_channel=chan))
    print(f'  {chan}: tes_bias_resistance = {r_bias} Ohm, '
          f'signal_gen_resistance = {r_sg} Ohm')

    # initial state (restored at the end)
    initial_bias = control.get_tes_bias(detector_channel=chan, unit='uA')
    initial_sg_on = control.is_signal_gen_on(detector_channel=chan)
    initial_output = output_state(inst)
    print(f'  Initial state: output {initial_output}, TES bias = {initial_bias:.4f} uA, '
          f'signal gen on = {initial_sg_on}')

    try:

        # ------------------------
        # TES bias
        # ------------------------
        print(f'\n2) TES bias = {args.bias} uA on {chan}')
        control.set_signal_gen_onoff('off', detector_channel=chan)
        control.set_tes_bias(args.bias, unit='uA', detector_channel=chan)

        check('TES bias readback [uA]',
              control.get_tes_bias(detector_channel=chan, unit='uA'), args.bias)
        check_equal('Output', output_state(inst), 'on')
        print(f'  instrument offset = {inst.get_offset(source=1, unit="V")*1e3:.4f} mV '
              f'(equivalent voltage), shape = {inst.get_shape(source=1)}')

        # ------------------------
        # dIdV signal generator
        # ------------------------
        print(f'\n3) dIdV: square {args.didv} mVpp, {args.freq} Hz')
        control.set_signal_gen_onoff('on', detector_channel=chan)
        control.set_signal_gen_params(detector_channel=chan, source='tes',
                                      voltage=args.didv, voltage_unit='mV',
                                      frequency=args.freq, frequency_unit='Hz',
                                      shape='square')

        check_equal('Signal gen on', control.is_signal_gen_on(detector_channel=chan), True)
        check_equal('Shape', sg.get_shape(source=1), 'square')
        check('Frequency [Hz]', sg.get_frequency(source=1, unit='Hz'), args.freq)
        check('Amplitude [mVpp]', sg.get_amplitude(source=1, unit='Vpp')*1e3, args.didv)
        check('TES bias unchanged [uA]',
              control.get_tes_bias(detector_channel=chan, unit='uA'), args.bias)

        params = control.get_signal_gen_params(detector_channel=chan)
        check('get_signal_gen_params current [uApp]',
              params['current']*1e6, args.didv*1e-3/r_sg*1e6)

        # ------------------------
        # signal generator off
        # ------------------------
        print('\n4) Signal generator off (bias kept)')
        control.set_signal_gen_onoff('off', detector_channel=chan)
        check_equal('Signal gen on', control.is_signal_gen_on(detector_channel=chan), False)
        check('TES bias unchanged [uA]',
              control.get_tes_bias(detector_channel=chan, unit='uA'), args.bias)
        print(f'  instrument: shape = {sg.get_shape(source=1)}, '
              f'frequency = {sg.get_frequency(source=1, unit="Hz"):.6g} Hz, '
              f'amplitude = {sg.get_amplitude(source=1, unit="Vpp")*1e3:.4f} mVpp')

    finally:

        # ------------------------
        # restore initial state
        # ------------------------
        print('\n5) Restore initial state')
        control.set_tes_bias(initial_bias, unit='uA', detector_channel=chan)
        control.set_signal_gen_onoff('on' if initial_sg_on else 'off',
                                     detector_channel=chan)
        inst.set_generator_onoff(initial_output, source=1)
        print(f'  output {output_state(inst)}, '
              f'TES bias = {control.get_tes_bias(detector_channel=chan, unit="uA"):.4f} uA, '
              f'signal gen on = {control.is_signal_gen_on(detector_channel=chan)}')

        control._disconnect_instruments()
        if setup_file != args.setup:
            os.remove(setup_file)

    # summary
    failed = [name for name, ok in results if not ok]
    print(f'\n{len(results)-len(failed)}/{len(results)} checks passed')
    for name in failed:
        print(f'  FAILED: {name}')
    sys.exit(0 if not failed else 1)
