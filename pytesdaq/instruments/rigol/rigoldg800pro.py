"""
Rigol DG800 Pro / DG900 Pro series function generator driver.

Fully compatible with the original RigolDG800 API (including `unit` and `level`
parameters) but uses the correct SCPI commands for the Pro series.
"""

import time
from enum import Enum
from pytesdaq.instruments.communication import InstrumentComm


class RigolDG800Pro(InstrumentComm):
    """
    Rigol DG800 Pro/DG900 Pro series function generators management.
    """

    def __init__(self, visa_address, visa_library='@py',
                 attenuation=1.0, raise_errors=True,
                 verbose=True):
        """
        Parameters
        ----------
        visa_address : str
            VISA resource name (e.g. 'USB0::0x1AB1::0x0641::...')
        visa_library : str
            VISA library backend (default '@py')
        attenuation : float
            Global amplitude scaling factor (default 1.0)
        raise_errors : bool
            Raise exceptions on command errors
        verbose : bool
            Print communication details
        """
        super().__init__(visa_address=visa_address, termination='\n',
                         visa_library=visa_library,
                         raise_errors=raise_errors,
                         verbose=verbose)

        self.connect()
        self._device_idn = self.get_idn()
        self._attenuation = attenuation
        self._raise_errors = raise_errors
        self._verbose = verbose

        if verbose:
            print(f"Connected to: {self._device_idn}")

    # ------------------------------------------------------------------
    # Basic properties
    # ------------------------------------------------------------------
    @property
    def device_idn(self):
        return self._device_idn

    # ------------------------------------------------------------------
    # Waveform shape (original API)
    # ------------------------------------------------------------------
    def set_shape(self, shape, source=1):
        """
        Set signal shape.

        Parameters
        ----------
        shape : str
            'sinusoid', 'sine', 'square', 'triangle', 'ramp', 'dc', 'arb', 'noise', 'pulse'
        source : int
            Channel (1 or 2)
        """
        shape_map = {
            'sinusoid': 'SIN',
            'sine': 'SIN',
            'square': 'SQU',
            'triangle': 'TRI',
            'ramp': 'RAMP',
            'dc': 'DC',
            'arb': 'ARB',
            'noise': 'NOIS',
            'pulse': 'PULS'
        }
        key = shape.lower()
        if key not in shape_map:
            raise ValueError(f"Shape '{shape}' not recognized. "
                             f"Valid: {list(shape_map.keys())}")
        self.write(f':SOUR{source}:FUNC {shape_map[key]}')

    def get_shape(self, source=1):
        """
        Get signal shape.

        Returns
        -------
        str
            Lowercase shape name (sine, square, triangle, ramp, pulse, noise, arb, dc)
        """
        resp = self.query(f':SOUR{source}:FUNC?').lower()
        rev_map = {
            'sin': 'sine',
            'squ': 'square',
            'tri': 'triangle',
            'ramp': 'ramp',
            'puls': 'pulse',
            'nois': 'noise',
            'arb': 'arb',
            'dc': 'dc'
        }
        return rev_map.get(resp, resp)

    # ------------------------------------------------------------------
    # Amplitude (original API with 'level' parameter)
    # ------------------------------------------------------------------
    def set_amplitude(self, amplitude, source=1, level='amp', unit='Vpp'):
        """
        Set signal amplitude.

        Parameters
        ----------
        amplitude : float
            Amplitude value
        source : int
            Channel (1 or 2)
        level : str
            'amp' (normal amplitude), 'low' (low level), or 'high' (high level)
        unit : str
            'Vpp', 'mVpp', 'Vrms', 'dbm'
        """
        amplitude = float(amplitude)

        # Unit handling
        unit_low = unit.lower()
        if unit_low == 'v':
            unit_low = 'vpp'
        elif unit_low == 'mv':
            unit_low = 'mvpp'

        if unit_low not in ('vpp', 'mvpp', 'vrms', 'dbm'):
            raise ValueError("Unit must be Vpp, mVpp, Vrms, or dbm")

        if unit_low == 'mvpp':
            amplitude /= 1000.0
            unit_low = 'vpp'

        if level not in ('amp', 'low', 'high'):
            raise ValueError("Level must be 'amp', 'low', or 'high'")

        # Apply global attenuation
        amplitude *= self._attenuation

        # Set amplitude unit (only for VPP/VRMS/DBM)
        self.write(f':SOUR{source}:VOLT:UNIT {unit_low.upper()}')

        # Set the appropriate voltage
        if level == 'amp':
            self.write(f':SOUR{source}:VOLT {amplitude}')
        elif level == 'low':
            self.write(f':SOUR{source}:VOLT:LOW {amplitude}')
        else:  # high
            self.write(f':SOUR{source}:VOLT:HIGH {amplitude}')

    def get_amplitude(self, source=1, level='amp', unit='Vpp'):
        """
        Get signal amplitude.

        Returns
        -------
        float
            Amplitude in the requested unit
        """
        unit_low = unit.lower()
        if unit_low == 'v':
            unit_low = 'vpp'
        elif unit_low == 'mv':
            unit_low = 'mvpp'

        if unit_low not in ('vpp', 'mvpp', 'vrms', 'dbm'):
            raise ValueError("Unit must be Vpp, mVpp, Vrms, or dbm")

        convert_mv = False
        if unit_low == 'mvpp':
            convert_mv = True
            unit_low = 'vpp'

        if level not in ('amp', 'low', 'high'):
            raise ValueError("Level must be 'amp', 'low', or 'high'")

        # Set unit for query
        self.write(f':SOUR{source}:VOLT:UNIT {unit_low.upper()}')

        # Query
        if level == 'amp':
            cmd = f':SOUR{source}:VOLT?'
        elif level == 'low':
            cmd = f':SOUR{source}:VOLT:LOW?'
        else:
            cmd = f':SOUR{source}:VOLT:HIGH?'

        value = float(self.query(cmd))
        value /= self._attenuation

        if convert_mv:
            value *= 1000.0
        return value

    # ------------------------------------------------------------------
    # Offset (original API with 'unit' parameter)
    # ------------------------------------------------------------------
    def set_offset(self, offset, source=1, unit='V'):
        """
        Set DC offset.

        Parameters
        ----------
        offset : float
            Offset value
        source : int
            Channel
        unit : str
            'V' or 'mV'
        """
        unit_low = unit.lower()
        if unit_low not in ('v', 'mv', 'vpp', 'mvpp'):
            raise ValueError("Unit must be V or mV")
        if unit_low == 'vpp':
            unit_low = 'v'
        if unit_low == 'mvpp':
            unit_low = 'mv'

        offs = float(offset)
        if unit_low == 'mv':
            offs /= 1000.0

        offs *= self._attenuation
        self.write(f':SOUR{source}:VOLT:OFFS {offs}')

    def get_offset(self, source=1, unit='V'):
        """
        Get DC offset.

        Returns
        -------
        float
            Offset in requested unit
        """
        unit_low = unit.lower()
        if unit_low not in ('v', 'mv', 'vpp', 'mvpp'):
            raise ValueError("Unit must be V or mV")
        if unit_low == 'vpp':
            unit_low = 'v'
        if unit_low == 'mvpp':
            unit_low = 'mv'

        value = float(self.query(f':SOUR{source}:VOLT:OFFS?'))
        value /= self._attenuation

        if unit_low == 'mv':
            value *= 1000.0
        return value

    # ------------------------------------------------------------------
    # Phase
    # ------------------------------------------------------------------
    def set_phase(self, phase, source=1):
        """Set phase in degrees."""
        self.write(f':SOUR{source}:PHAS {phase}')

    def get_phase(self, source=1):
        """Get phase in degrees."""
        return float(self.query(f':SOUR{source}:PHAS?'))

    # ------------------------------------------------------------------
    # Frequency
    # ------------------------------------------------------------------
    def set_frequency(self, frequency, source=1, unit='Hz'):
        """
        Set frequency.

        Parameters
        ----------
        frequency : float
        unit : str
            'Hz', 'kHz', 'MHz'
        """
        freq = float(frequency)
        if unit == 'kHz':
            freq *= 1000.0
        elif unit == 'MHz':
            freq *= 1e6
        elif unit != 'Hz':
            raise ValueError("Unit must be Hz, kHz, or MHz")
        self.write(f':SOUR{source}:FREQ {freq}')

    def get_frequency(self, source=1, unit='Hz'):
        """
        Get frequency.

        Returns
        -------
        float
            Frequency in requested unit
        """
        freq = float(self.query(f':SOUR{source}:FREQ?'))
        if unit == 'kHz':
            freq /= 1000.0
        elif unit == 'MHz':
            freq /= 1e6
        return freq

    # ------------------------------------------------------------------
    # Pulse width (original API)
    # ------------------------------------------------------------------
    def set_pulse_width(self, width, source=1):
        """Set pulse width in seconds."""
        self.write(f':SOUR{source}:FUNC:PULS:WIDT {width}')

    def get_pulse_width(self, source=1):
        """Get pulse width in seconds."""
        return float(self.query(f':SOUR{source}:FUNC:PULS:WIDT?'))

    # ------------------------------------------------------------------
    # Output on/off
    # ------------------------------------------------------------------
    def set_generator_onoff(self, output_onoff, source=1):
        """
        Turn output on/off.

        Parameters
        ----------
        output_onoff : str or int
            'on', 'off', 1, 0
        """
        if isinstance(output_onoff, int):
            state = 1 if output_onoff else 0
        else:
            state = 1 if output_onoff.lower() == 'on' else 0
        self.write(f'OUTP{source} {state}')

    def get_generator_onoff(self, source=1):
        """Return True if output is enabled."""
        return int(self.query(f'OUTP{source}?')) == 1

    # ------------------------------------------------------------------
    # Load resistance
    # ------------------------------------------------------------------
    def set_load_resistance(self, load, source=1):
        """
        Set output load impedance.

        Parameters
        ----------
        load : int or str
            Resistance in ohms, or 'INF' for high impedance
        """
        if isinstance(load, str):
            load = load.upper()
            if load not in ('INF', 'INFINITY'):
                raise ValueError("Load string must be 'INF' or 'INFINITY'")
        self.write(f'OUTP{source}:IMP {load}')

    def get_load_resistance(self, source=1):
        """Return load impedance (string, may be '9.9E+37' for high Z)."""
        return self.query(f'OUTP{source}:IMP?')

    # ------------------------------------------------------------------
    # Phase alignment (original)
    # ------------------------------------------------------------------
    def align_phase(self, source=2, reference_source=1):
        """
        Align phase between two channels.

        Parameters
        ----------
        source : int
            Channel to align (default 2)
        reference_source : int
            Reference channel (default 1)
        """
        if source == reference_source:
            raise ValueError("source and reference_source must be different")
        # Use the Pro‑series phase synchronisation command
        self.write(f':SOUR{reference_source}:PHAS:INIT')
        self.write(f':SOUR{source}:PHAS:SYNC')

    # ------------------------------------------------------------------
    # Additional Pro‑series methods (new features)
    # ------------------------------------------------------------------
    def reset(self):
        """Factory reset."""
        self.write('*RST')

    def clear_status(self):
        """Clear event registers and error queue."""
        self.write('*CLS')

    def apply_sine(self, frequency, amplitude, offset=0, phase=0, source=1):
        """Quick sine wave setup."""
        self.write(f':SOUR{source}:APPL:SIN {frequency},{amplitude},{offset},{phase}')

    def apply_square(self, frequency, amplitude, offset=0, phase=0, source=1):
        """Quick square wave setup."""
        self.write(f':SOUR{source}:APPL:SQU {frequency},{amplitude},{offset},{phase}')

    def apply_ramp(self, frequency, amplitude, offset=0, phase=0, source=1):
        """Quick ramp wave setup."""
        self.write(f':SOUR{source}:APPL:RAMP {frequency},{amplitude},{offset},{phase}')

    def apply_pulse(self, frequency, amplitude, offset=0, phase=0, source=1):
        """Quick pulse wave setup."""
        self.write(f':SOUR{source}:APPL:PULS {frequency},{amplitude},{offset},{phase}')

    def apply_noise(self, amplitude, offset=0, source=1):
        """Quick noise setup."""
        self.write(f':SOUR{source}:APPL:NOIS 100,{amplitude},{offset},0')

    def apply_dc(self, offset, source=1):
        """Quick DC setup."""
        self.write(f':SOUR{source}:APPL:DC 100,5,{offset},0')

    # ---- Sweep ---------------------------------------------------------
    def enable_sweep(self, source=1):
        self.write(f':SOUR{source}:SWE:STAT ON')

    def disable_sweep(self, source=1):
        self.write(f':SOUR{source}:SWE:STAT OFF')

    def set_sweep_parameters(self, start_freq, stop_freq, sweep_time,
                             sweep_type='LIN', source=1):
        """Configure frequency sweep."""
        self.write(f':SOUR{source}:FREQ:STAR {start_freq}')
        self.write(f':SOUR{source}:FREQ:STOP {stop_freq}')
        self.write(f':SOUR{source}:SWE:TIME {sweep_time}')
        self.write(f':SOUR{source}:SWE:SPAC {sweep_type.upper()}')

    # ---- Burst ---------------------------------------------------------
    def enable_burst(self, source=1):
        self.write(f':SOUR{source}:BURS:STAT ON')

    def disable_burst(self, source=1):
        self.write(f':SOUR{source}:BURS:STAT OFF')

    def set_burst_parameters(self, burst_count, burst_period, source=1):
        """Configure N‑cycle burst."""
        self.write(f':SOUR{source}:BURS:NCYC {burst_count}')
        self.write(f':SOUR{source}:BURS:INT:PER {burst_period}')

    # ---- Counter -------------------------------------------------------
    def enable_counter(self):
        self.write(':COUN:STAT ON')

    def disable_counter(self):
        self.write(':COUN:STAT OFF')

    def get_counter_measurement(self):
        """Return dict with frequency, period, duty cycle, widths."""
        resp = self.query(':COUN:MEAS?')
        parts = resp.split(',')
        if len(parts) >= 5:
            return {
                'frequency': float(parts[0]),
                'period': float(parts[1]),
                'duty_cycle': float(parts[2]),
                'positive_width': float(parts[3]),
                'negative_width': float(parts[4])
            }
        return None

    # ---- Arbitrary waveforms -------------------------------------------
    def set_arbitrary_waveform(self, waveform_name, source=1):
        """Select a built‑in arbitrary waveform by name."""
        self.write(f':SOUR{source}:FUNC:ARB {waveform_name}')

    def download_arbitrary_data(self, data_points, source=1):
        """
        Download arbitrary waveform data points (int -32768..32767).
        """
        if not data_points:
            return
        data_str = ','.join(str(int(x)) for x in data_points)
        self.write(f':SOUR{source}:TRAC:DATA:DAC16 CODE,END,{data_str}')

    # ---- Harmonic ------------------------------------------------------
    def set_harmonic_type(self, harmonic_type, source=1):
        """Set harmonic type: ORDer or COMBine."""
        self.write(f':SOUR{source}:HARM:TYPE {harmonic_type.upper()}')

    def set_harmonic_order(self, order, source=1):
        """Set which harmonic order to output (2..20)."""
        self.write(f':SOUR{source}:HARM:ORD {order}')

    # ---- Modulation (AM) -----------------------------------------------
    def enable_am(self, source=1):
        self.write(f':SOUR{source}:AM:STAT ON')

    def disable_am(self, source=1):
        self.write(f':SOUR{source}:AM:STAT OFF')

    def set_am_depth(self, depth_percent, source=1):
        self.write(f':SOUR{source}:AM:DEPT {depth_percent}')

    # ---- Utility -------------------------------------------------------
    def get_error_queue(self):
        """Return (error_code, error_message)."""
        resp = self.query(':SYST:ERR?')
        parts = resp.split(',')
        if len(parts) >= 2:
            return int(parts[0]), parts[1].strip('"')
        return 0, "No error"

    def take_screenshot(self, fmt='PNG'):
        """Capture screen and return binary data."""
        fmt = fmt.upper()
        if fmt not in ('BMP', 'PNG'):
            raise ValueError("Format must be BMP or PNG")
        self.write(f':HCOPY:SDUMP:DATA:FORM {fmt}')
        return self.query_binary(':HCOPY:SDUMP:DATA?')

    # ---- Place holder for iv and didv -----------------------------------

    def set_auto_range(self, auto, source=1):
        return

# For backward compatibility with old code that expects 'RigolDG800'
#RigolDG800 = RigolDG800Pro
