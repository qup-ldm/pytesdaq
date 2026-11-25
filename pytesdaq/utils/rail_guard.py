"""
SQUID rail guard: between data taking runs, read the channel
baselines and reset the SQUIDs (Control.reset_squid(), e.g. PCS102
remote) if any baseline is close to the ADC rail.

Used by the IV/dIdV sequencer and by DAQControl (run_daq.py).

Configuration keys (sequencer iv_didv.ini [iv_didv], daq.ini
acquisition sections):
   rail_guard = true/false
   rail_fraction = 0.75     (of ADC voltage range)
   rail_nb_traces = 10      (traces of 5000 samples per check)
   rail_max_resets = 2      (max resets per check)
   rail_reset_wait = 0.5    (seconds after reset before re-check)
"""

import copy
import time
import numpy as np
from pytesdaq.daq import daq


def read_online_traces(config, adc_channel_dict,
                       nb_events=100, nb_samples=5000):
    """
    Short continuous read (pydaqmx) between Polaris runs

    Args:
       config: settings.Config (default ADC setup)
       adc_channel_dict: {adc_id: [adc channels]}

    Returns:
       data_array [events, channels, samples] (volts),
       sample_rate
    """
    daq_online = daq.DAQ(driver_name='pydaqmx', verbose=False)
    sample_rate = None
    setup_dict = dict()
    for adc_id, adc_list in adc_channel_dict.items():
        setup_dict[adc_id] = copy.deepcopy(config.get_adc_setup(adc_id))
        setup_dict[adc_id]['nb_samples'] = nb_samples
        setup_dict[adc_id]['channel_list'] = list(adc_list)
        setup_dict[adc_id]['trigger_type'] = 3
        sample_rate = setup_dict[adc_id]['sample_rate']
    daq_online.set_adc_config_from_dict(setup_dict)
    try:
        data_array = daq_online.read_many_events(nb_events,
                                                 adctovolt=True)
    finally:
        daq_online.clear()
    return data_array, sample_rate


def _to_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ('true', 'yes', '1')
    return bool(value)


class RailGuard:
    """
    Check channel baselines against the ADC rail, reset SQUIDs
    if railed
    """

    def __init__(self, enabled=False, fraction=0.75, nb_traces=10,
                 max_resets=2, reset_wait=0.5):
        self.enabled = bool(enabled)
        self.fraction = float(fraction)
        self.nb_traces = int(nb_traces)
        self.max_resets = int(max_resets)
        self.reset_wait = float(reset_wait)

    @classmethod
    def from_config(cls, config_dict):
        """
        Build from configuration dictionary ("rail_*" keys,
        strings or numbers, missing keys -> defaults)
        """
        guard = cls()
        if not config_dict:
            return guard
        guard.enabled = _to_bool(config_dict.get('rail_guard', False))
        guard.fraction = float(config_dict.get('rail_fraction',
                                               guard.fraction))
        guard.nb_traces = int(float(config_dict.get('rail_nb_traces',
                                                    guard.nb_traces)))
        guard.max_resets = int(float(config_dict.get('rail_max_resets',
                                                     guard.max_resets)))
        guard.reset_wait = float(config_dict.get('rail_reset_wait',
                                                 guard.reset_wait))
        return guard

    def __str__(self):
        return (f'SQUID rail guard: reset if |baseline| > '
                f'{self.fraction:.0%} of ADC range, '
                f'{self.nb_traces} traces per check, '
                f'max {self.max_resets} resets')

    def check(self, control, config, adc_setup):
        """
        Read baselines of the channels in adc_setup
        ({adc_id: {'channel_list', 'voltage_min', 'voltage_max',...}}),
        reset SQUIDs (all channels) if any |baseline| >
        fraction x ADC range, re-check, up to max_resets

        Returns:
           (ok, nb_resets)
        """
        if not self.enabled:
            return True, 0

        rail = min(min(abs(float(d['voltage_min'])),
                       abs(float(d['voltage_max'])))
                   for d in adc_setup.values())
        threshold = self.fraction * rail

        adc_channel_dict = {adc_id: list(d['channel_list'])
                            for adc_id, d in adc_setup.items()}
        labels = [f'{adc_id}:{chan}'
                  for adc_id, chans in adc_channel_dict.items()
                  for chan in chans]

        railed = list()
        for nb_resets in range(self.max_resets + 1):
            data, _ = read_online_traces(config, adc_channel_dict,
                                         nb_events=self.nb_traces)
            baselines = np.mean(data, axis=(0, 2))
            railed = [f'{label} ({v:.2f} V)' for label, v in
                      zip(labels, baselines) if abs(v) > threshold]
            if not railed:
                return True, nb_resets
            if nb_resets == self.max_resets:
                break
            print(f'WARNING: SQUID railed (|V| > {threshold:.2f} V): '
                  f'{", ".join(railed)}. Resetting SQUIDs!')
            try:
                control.reset_squid()
            except Exception as e:
                print(f'ERROR: SQUID reset failed: {e}')
                return False, nb_resets
            time.sleep(self.reset_wait)

        print(f'ERROR: SQUID still railed after {nb_resets} resets: '
              + ', '.join(railed))
        return False, nb_resets
