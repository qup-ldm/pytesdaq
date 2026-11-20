"""
Tests for the TES bias ramp (soft transition between bias points).

No hardware needed: Control is used in dummy mode with set/get
monkeypatched, the sequencer / DAQControl are only configured.

Usage:
    python testing/test_tes_bias_ramp.py
"""
import atexit
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

import pytesdaq.instruments.control as control
from pytesdaq.utils import arg_utils

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix='pytesdaq_ramp_test_')
atexit.register(shutil.rmtree, TMP, True)


def _override_keys(text, overrides, count=1):
    """
    Replace "key = value" lines (None -> remove key). Each key must
    appear "count" times (daq.ini: once per [iv]/[didv] section)
    """
    for key, value in overrides.items():
        if value is None:
            text, n = re.subn(r'^' + key + r'\s*=.*$\n?', '', text, flags=re.M)
        else:
            text, n = re.subn(r'^' + key + r'\s*=.*$',
                              f'{key} = {value}', text, flags=re.M)
        assert n == count, key
    return text


def make_configs(iv_didv_overrides=None, daq_overrides=None):
    """
    Copy setup.ini / iv_didv.ini / daq.ini in a temporary directory,
    redirect data_path, optionally override [iv_didv] keys and daq
    [iv]/[didv] keys (so tests do not depend on the tunable values
    in the repo ini files)
    """
    setup_src = os.path.join(REPO, 'pytesdaq/config/setup.ini')
    seq_src = os.path.join(REPO, 'pytesdaq/config/iv_didv.ini')
    daq_src = os.path.join(REPO, 'pytesdaq/config/daq.ini')
    setup_dst = os.path.join(TMP, 'setup.ini')
    seq_dst = os.path.join(TMP, 'iv_didv.ini')
    daq_dst = os.path.join(TMP, 'daq.ini')

    with open(setup_src) as f:
        setup = f.read()
    setup = re.sub(r'^data_path\s*=.*$',
                   'data_path = ' + os.path.join(TMP, 'data'),
                   setup, flags=re.M)
    with open(setup_dst, 'w') as f:
        f.write(setup)

    with open(seq_src) as f:
        seq = f.read()
    if iv_didv_overrides:
        seq = _override_keys(seq, iv_didv_overrides)
    with open(seq_dst, 'w') as f:
        f.write(seq)

    with open(daq_src) as f:
        daq = f.read()
    if daq_overrides:
        daq = _override_keys(daq, daq_overrides, count=2)
    with open(daq_dst, 'w') as f:
        f.write(daq)

    return setup_dst, seq_dst, daq_dst


class TestRampValues(unittest.TestCase):

    def test_basic_down(self):
        vals = control.Control._get_ramp_values(600, 585, 5)
        self.assertEqual(vals, [595, 590, 585])

    def test_basic_up(self):
        vals = control.Control._get_ramp_values(0, 10, 4)
        # 3 equal steps of 3.333 (<= 4), ending exactly at 10
        self.assertEqual(len(vals), 3)
        self.assertAlmostEqual(vals[-1], 10.0)
        incs = np.diff([0] + vals)
        self.assertTrue(np.all(incs <= 4 + 1e-12))
        self.assertTrue(np.allclose(incs, incs[0]))

    def test_no_ramp_needed(self):
        self.assertEqual(control.Control._get_ramp_values(10, 10, 1), [10])
        self.assertEqual(control.Control._get_ramp_values(10, 9.5, 1), [9.5])
        self.assertEqual(control.Control._get_ramp_values(10, 9, 1), [9])

    def test_unknown_start(self):
        self.assertEqual(control.Control._get_ramp_values(None, 7, 1), [7])
        self.assertEqual(control.Control._get_ramp_values(np.nan, 7, 1), [7])
        self.assertEqual(control.Control._get_ramp_values(0, 7, 0), [7])
        self.assertEqual(control.Control._get_ramp_values(0, 7, None), [7])

    def test_negative_bias(self):
        vals = control.Control._get_ramp_values(-20, -23, 1)
        self.assertEqual(vals, [-21, -22, -23])
        vals = control.Control._get_ramp_values(5, -5, 2.5)
        self.assertEqual(vals, [2.5, 0, -2.5, -5])

    def test_float_tolerance(self):
        vals = control.Control._get_ramp_values(0, 0.3, 0.1)
        self.assertEqual(len(vals), 3)
        self.assertAlmostEqual(vals[-1], 0.3)
        vals = control.Control._get_ramp_values(20, 0, 3)
        self.assertEqual(len(vals), 7)
        self.assertTrue(np.all(np.abs(np.diff([20] + vals)) <= 3 + 1e-12))
        self.assertEqual(vals[-1], 0)

    def test_last_is_exact_target(self):
        vals = control.Control._get_ramp_values(600.0, 300.0, 15.0)
        self.assertEqual(len(vals), 20)
        self.assertEqual(vals[-1], 300.0)
        self.assertNotIn(600.0, vals)


class TestExtractRampConfig(unittest.TestCase):

    def test_disabled_default(self):
        self.assertEqual(arg_utils.extract_tes_bias_ramp_config({}),
                         (None, 0.1))
        self.assertEqual(arg_utils.extract_tes_bias_ramp_config(None),
                         (None, 0.1))
        self.assertEqual(arg_utils.extract_tes_bias_ramp_config(
            {'use_tes_bias_ramp': False, 'tes_bias_ramp_step': 2}),
            (None, 0.1))
        self.assertEqual(arg_utils.extract_tes_bias_ramp_config(
            {'use_tes_bias_ramp': 'false', 'tes_bias_ramp_step': 2}),
            (None, 0.1))

    def test_enabled_types(self):
        # as produced by settings.py: bool / float / str ('0.5' stays str)
        self.assertEqual(arg_utils.extract_tes_bias_ramp_config(
            {'use_tes_bias_ramp': True, 'tes_bias_ramp_step': 2.0,
             'tes_bias_ramp_step_time': '0.05'}),
            (2.0, 0.05))
        self.assertEqual(arg_utils.extract_tes_bias_ramp_config(
            {'use_tes_bias_ramp': 'True', 'tes_bias_ramp_step': '0.5'}),
            (0.5, 0.1))

    def test_errors(self):
        with self.assertRaises(ValueError):
            arg_utils.extract_tes_bias_ramp_config({'use_tes_bias_ramp': True})
        with self.assertRaises(ValueError):
            arg_utils.extract_tes_bias_ramp_config(
                {'use_tes_bias_ramp': True, 'tes_bias_ramp_step': 0})
        with self.assertRaises(ValueError):
            arg_utils.extract_tes_bias_ramp_config(
                {'use_tes_bias_ramp': True, 'tes_bias_ramp_step': 1,
                 'tes_bias_ramp_step_time': -1})


class FakeSource:
    """ Records set_tes_bias calls, answers get_tes_bias """

    def __init__(self, present):
        self.present = dict(present)   # per channel
        self.calls = list()

    def get(self, unit=None, tes_channel=None, detector_channel=None,
            adc_id=None, adc_channel=None):
        value = self.present[detector_channel]
        if isinstance(value, Exception):
            raise value
        return value

    def set(self, bias, unit=None, use_net_resistance=None,
            tes_channel=None, detector_channel=None,
            adc_id=None, adc_channel=None):
        self.calls.append((detector_channel, float(bias), unit,
                           use_net_resistance))
        self.present[detector_channel] = float(bias)
        return True


class TestRampTesBias(unittest.TestCase):

    def setUp(self):
        setup_file, _, _ = make_configs()
        self.inst = control.Control(setup_file=setup_file,
                                    dummy_mode=True, verbose=True)
        self.inst._tes_controller_inst = mock.Mock()   # "available"

    def _patch(self, source):
        self.inst.get_tes_bias = source.get
        self.inst.set_tes_bias = source.set

    def test_ramp_sequence_with_sleep(self):
        src = FakeSource({'TES1': 600.0})
        self._patch(src)
        self.inst._dummy_mode = False
        with mock.patch.object(control.time, 'sleep') as sleep:
            ok = self.inst.ramp_tes_bias(585, unit='uA', ramp_step=5,
                                         ramp_step_time=0.2,
                                         detector_channel='TES1',
                                         use_net_resistance=True)
        self.assertTrue(ok)
        self.assertEqual([c[1] for c in src.calls], [595, 590, 585])
        self.assertTrue(all(c[2] == 'uA' and c[3] is True for c in src.calls))
        # sleep between steps only (not after the last)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(0.2)

    def test_no_sleep_in_dummy_mode(self):
        src = FakeSource({'TES1': 0.0})
        self._patch(src)
        with mock.patch.object(control.time, 'sleep') as sleep:
            self.inst.ramp_tes_bias(10, unit='uA', ramp_step=1,
                                    ramp_step_time=0.5,
                                    detector_channel='TES1')
        self.assertEqual(len(src.calls), 10)
        self.assertEqual(sleep.call_count, 0)

    def test_disabled_is_direct_set(self):
        src = FakeSource({'TES1': 600.0})
        self._patch(src)
        for step in [None, 0, -1]:
            src.calls.clear()
            self.inst.ramp_tes_bias(0, unit='uA', ramp_step=step,
                                    detector_channel='TES1')
            self.assertEqual([c[1] for c in src.calls], [0.0])

    def test_already_within_one_step(self):
        src = FakeSource({'TES1': 600.0})
        self._patch(src)
        self.inst.ramp_tes_bias(600, unit='uA', ramp_step=5,
                                detector_channel='TES1')
        self.inst.ramp_tes_bias(596, unit='uA', ramp_step=5,
                                detector_channel='TES1')
        # always at least one set (target), never an intermediate
        self.assertEqual([c[1] for c in src.calls], [600.0, 596.0])

    def test_readback_failure_sets_directly(self):
        src = FakeSource({'TES1': RuntimeError('comm error')})
        self._patch(src)
        ok = self.inst.ramp_tes_bias(100, unit='uA', ramp_step=1,
                                     detector_channel='TES1')
        self.assertTrue(ok)
        self.assertEqual([c[1] for c in src.calls], [100.0])

        src = FakeSource({'TES1': float('nan')})
        self._patch(src)
        self.inst.ramp_tes_bias(100, unit='uA', ramp_step=1,
                                detector_channel='TES1')
        self.assertEqual([c[1] for c in src.calls], [100.0])

    def test_from_bias_overrides_readback(self):
        src = FakeSource({'TES1': 0.0})
        self._patch(src)
        self.inst.ramp_tes_bias(10, unit='uA', ramp_step=5,
                                from_bias=20, detector_channel='TES1')
        self.assertEqual([c[1] for c in src.calls], [15.0, 10.0])

    def test_mv_unit_and_negative_sweep(self):
        src = FakeSource({'TES1': -20.0})
        self._patch(src)
        self.inst.ramp_tes_bias(-23, unit='mV', ramp_step=1,
                                detector_channel='TES1')
        self.assertEqual([c[1] for c in src.calls], [-21.0, -22.0, -23.0])
        self.assertTrue(all(c[2] == 'mV' for c in src.calls))

    def test_set_failure_reported(self):
        src = FakeSource({'TES1': 0.0})
        self._patch(src)
        self.inst.set_tes_bias = lambda *a, **k: False
        ok = self.inst.ramp_tes_bias(3, unit='uA', ramp_step=1,
                                     detector_channel='TES1')
        self.assertFalse(ok)

    def test_bad_unit(self):
        src = FakeSource({'TES1': 0.0})
        self._patch(src)
        with self.assertRaises(ValueError):
            self.inst.ramp_tes_bias(3, unit='V', ramp_step=1,
                                    detector_channel='TES1')

    def test_sequential_channels_shared_source(self):
        # two channels on a shared source: once channel 1 has ramped,
        # channel 2 reads back the target -> single set, no second ramp
        class Shared(FakeSource):
            def get(self, **kw):
                return self.present['shared']
            def set(self, bias, **kw):
                self.calls.append((kw['detector_channel'], float(bias)))
                self.present['shared'] = float(bias)
                return True
        src = Shared({'shared': 0.0})
        self._patch(src)
        for chan in ['TES1', 'TES2']:
            self.inst.ramp_tes_bias(3, unit='uA', ramp_step=1,
                                    detector_channel=chan)
        self.assertEqual(src.calls, [('TES1', 1.0), ('TES1', 2.0),
                                     ('TES1', 3.0), ('TES2', 3.0)])

    def test_m81_net_load_frame(self):
        # fresh instance: readback converted with pre-calculated net load
        self.inst._tes_controller_name = 'lakeshoreM81'
        self.inst._m81_voltage_source = False
        self.inst._m81_tes_net_load = None
        self.inst._tes_controller_load = 500.0
        src = FakeSource({'TES1': 0.0})
        self._patch(src)
        self.inst.ramp_tes_bias(1, unit='uA', ramp_step=1,
                                detector_channel='TES1',
                                use_net_resistance=True)
        self.assertEqual(self.inst._m81_tes_net_load, 500.0)
        # not touched without use_net_resistance
        self.inst._m81_tes_net_load = None
        self.inst.ramp_tes_bias(1, unit='uA', ramp_step=1,
                                detector_channel='TES1',
                                use_net_resistance=False)
        self.assertIsNone(self.inst._m81_tes_net_load)


class TestSequencerConfig(unittest.TestCase):

    def _sequencer(self, overrides=None):
        from pytesdaq.sequencer.iv_didv import IV_dIdV
        setup_file, seq_file, _ = make_configs(overrides)
        return IV_dIdV(iv=True, didv=True, dummy_mode=True,
                       sweep_channels=['TES1', 'TES2'],
                       setup_file=setup_file, sequencer_file=seq_file,
                       verbose=False)

    def test_ramp_enabled(self):
        seq = self._sequencer({'use_tes_bias_ramp': 'true',
                               'tes_bias_ramp_step': '2',
                               'tes_bias_ramp_step_time': '0.1'})
        self.assertEqual(seq._tes_bias_ramp_step, 2.0)
        self.assertEqual(seq._tes_bias_ramp_step_time, 0.1)

    def test_ramp_disabled(self):
        seq = self._sequencer({'use_tes_bias_ramp': 'false'})
        self.assertIsNone(seq._tes_bias_ramp_step)

    def test_ramp_values_and_fraction(self):
        seq = self._sequencer({'tes_bias_ramp_step': '0.5',
                               'tes_bias_ramp_step_time': '0.25'})
        self.assertEqual(seq._tes_bias_ramp_step, 0.5)
        self.assertEqual(seq._tes_bias_ramp_step_time, 0.25)

    def test_missing_key_removed(self):
        # no ramp keys at all -> disabled (backward compatible)
        seq = self._sequencer({'use_tes_bias_ramp': None,
                               'tes_bias_ramp_step': None,
                               'tes_bias_ramp_step_time': None})
        self.assertIsNone(seq._tes_bias_ramp_step)

    def test_missing_step_error(self):
        with self.assertRaises(ValueError):
            self._sequencer({'tes_bias_ramp_step': None})


class TestDAQControlConfig(unittest.TestCase):

    def test_iv_didv_config(self):
        import pytesdaq.daq.daqcontrol as daqcontrol
        setup_file, _, daq_file = make_configs(
            daq_overrides={'use_tes_bias_ramp': 'true',
                           'tes_bias_ramp_step': '10',
                           'tes_bias_ramp_step_time': '0.1'})

        # daqcontrol.instrument is the control module itself: keep a
        # reference to the real class before patching
        RealControl = control.Control

        def dummy_control(setup_file=None, verbose=True, **kw):
            return RealControl(setup_file=setup_file, verbose=verbose,
                               dummy_mode=True)

        with mock.patch.object(daqcontrol.instrument, 'Control',
                               dummy_control):
            ctrl = daqcontrol.DAQControl('iv', setup_file=setup_file,
                                         daq_config_file=daq_file,
                                         channels=['TES1'],
                                         verbose=False)
        cfg = ctrl._iv_didv_config
        self.assertEqual(cfg['iv']['tes_bias_ramp_step'], 10.0)
        self.assertEqual(cfg['iv']['tes_bias_ramp_step_time'], 0.1)
        self.assertEqual(cfg['didv']['tes_bias_ramp_step'], 10.0)
        self.assertEqual(cfg['didv']['tes_bias_ramp_step_time'], 0.1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
