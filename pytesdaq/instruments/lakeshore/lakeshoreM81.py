"""
Lakeshore M81 Synchronous Source Measure (SSM) system

Adapted from the Model 372 driver (lakeshore.py). The M81 is not a
scanning resistance bridge: it has up to 3 source modules (S1-S3,
e.g. BCS-10, CS-10, VS-10) and up to 3 measure modules (M1-M3,
e.g. VM-10, CM-10). The pytesdaq "channel" concepts are mapped as:

  - "resistance" channels 1-3  -> measure modules M1-M3
        Resistance is computed by the instrument (CALC:SENS:RES) using
        a paired source module (configured with "source:S1" etc.)
  - "heater" channels 1-3      -> source modules S1-S3
        Used as DC current (or voltage) sources

The M81 has no scanner, no calibration curves and no PID loop, so
scan / PID / temperature-setpoint functions are not available.

Setup file example ([temperature_controllers] section):

  lakeshoreM81_setup = model:M81, com_port:/dev/ttyUSB0, baud_rate:921600
  lakeshoreM81_chan1 = name:RTD1, global_number:1, source:S1
  lakeshoreM81_heater1 = name:heaterS1, global_number:31
"""

from lakeshore import SSMSystem, XIPInstrumentException
import pandas as pd
import os
from pytesdaq.config import settings


# M81 constants
_MAX_NB_CHANNELS = 3          # measure modules M1-M3
_MAX_NB_HEATER_CHANNELS = 3   # source modules S1-S3
_DEFAULT_COM_PORT = '/dev/ttyUSB0'
_DEFAULT_BAUD_RATE = 921600
_DEFAULT_TCP_PORT = 7777
_SOURCE_NAMES = ['S1', 'S2', 'S3']


class LakeshoreM81():
    """
    Lakeshore M81 Synchronous Source Measure system
    """

    def __init__(self, instrument_name='lakeshoreM81', model_number='M81',
                 ip_address=None, tcp_port=None, com_port=_DEFAULT_COM_PORT,
                 baud_rate=_DEFAULT_BAUD_RATE, raise_errors=True, verbose=True):

        # instrument name and number (e.g. "lakeshore2" -> 2)
        self._instrument_name = instrument_name
        self._instrument_number = None
        try:
            if self._instrument_name[9:]:
                self._instrument_number = int(self._instrument_name[9:])
        except ValueError:
            self._instrument_number = None

        # verbose
        self._debug = False
        self._verbose = verbose
        self._raise_errors = raise_errors

        # Initialize instrument
        self._inst = None
        self._model_number = model_number
        self._serial_number = None
        self._firmware_version = None

        # Initialize connection setup
        self._ip_address = ip_address
        self._baud_rate = baud_rate
        self._tcp_port = tcp_port
        self._com_port = com_port

        # resistance source (S1..S3) per measure channel, from config
        self._resistance_sources = dict()

        # channel properties
        self._channel_properties = ['instrument_name', 'module_name', 'module_type', 'module_number',
                                    'module_address', 'channel_number', 'global_channel_number',
                                    'channel_name', 'device_type', 'device_serial']
        # resistance channels
        self._resistance_channel_table = None
        self._resistance_channel_property_list = None
        self._resistance_channel_names = None
        self._resistance_global_channel_numbers = None

        # heater channels
        self._heater_channel_table = None
        self._heater_channel_property_list = None
        self._heater_channel_names = None
        self._heater_global_channel_numbers = None

    @property
    def model_number(self):
        return self._model_number

    @property
    def serial_number(self):
        return self._serial_number

    @property
    def firmware_version(self):
        return self._firmware_version

    @property
    def ip_address(self):
        return self._ip_address

    @property
    def tcp_port(self):
        return self._tcp_port

    @property
    def com_port(self):
        return self._com_port

    @property
    def instrument(self):
        """
        Underlying lakeshore.SSMSystem object (for advanced use)
        """
        return self._inst

    @property
    def resistance_channel_names(self):
        return self._resistance_channel_names

    @property
    def resistance_global_channel_numbers(self):
        return self._resistance_global_channel_numbers

    @property
    def heater_channel_names(self):
        return self._heater_channel_names

    @property
    def heater_global_channel_numbers(self):
        return self._heater_global_channel_numbers

    def get_channel_table(self, channel_type=None):
        """
        Return pandas table
        """

        if channel_type == 'resistance':
            return self._resistance_channel_table
        elif channel_type == 'heater':
            return self._heater_channel_table
        elif channel_type is None:
            tables = list()
            if self._resistance_channel_table is not None:
                tables.append(self._resistance_channel_table)
            if self._heater_channel_table is not None:
                tables.append(self._heater_channel_table)
            if not tables:
                return pd.DataFrame(columns=self._channel_properties)
            return pd.concat(tables)
        else:
            raise ValueError('ERROR: channel type should be "resistance", "heater" or None!')

    def setup_instrument_from_config(self, setup_file):
        """
        Setup lakeshore M81 from setup file
        """

        if setup_file is None or not os.path.isfile(setup_file):
            raise ValueError('ERROR: Setup file not found!')

        # get configuration
        config = settings.Config(setup_file=setup_file)
        # (config parser lowercases keys -> lowercase instrument name)
        lakeshore_setup = config.get_temperature_controller_setup(self._instrument_name.lower())

        if not lakeshore_setup or 'setup' not in lakeshore_setup:
            raise ValueError('ERROR: No "' + self._instrument_name + '_setup" entry found in '
                             + '[temperature_controllers] section of setup file!')

        try:

            # connection setup
            setup_dict = lakeshore_setup['setup']

            if 'model' in setup_dict:
                self._model_number = str(setup_dict['model']).strip()
            else:
                raise ValueError('ERROR: Missing lakeshore model number in setup file!')

            if 'ip' in setup_dict:
                self._ip_address = setup_dict['ip']
                self._tcp_port = int(setup_dict.get('tcp_port', _DEFAULT_TCP_PORT))
            if 'com_port' in setup_dict:
                self._com_port = setup_dict['com_port']
            if 'baud_rate' in setup_dict:
                self._baud_rate = int(setup_dict['baud_rate'])

            # channel setup
            for chan_type in ['resistance', 'heater']:
                for chan in range(0, _MAX_NB_CHANNELS+1):

                    # parameter name
                    item_name = 'chan' + str(chan)
                    if chan_type == 'heater':
                        item_name = 'heater' + str(chan)

                    if item_name not in lakeshore_setup:
                        continue

                    if chan < 1:
                        raise ValueError('ERROR: M81 channel numbers start at 1 ('
                                         + item_name + ')!')

                    chan_dict = lakeshore_setup[item_name]

                    chan_name = chan_dict.get('name', None)
                    chan_number = None
                    if 'global_number' in chan_dict:
                        chan_number = int(chan_dict['global_number'])
                    device_type = chan_dict.get('type', None)
                    device_serial = chan_dict.get('serial', None)

                    # resistance source module (measure channels only)
                    if chan_type == 'resistance' and 'source' in chan_dict:
                        source = str(chan_dict['source']).strip().upper()
                        if source not in _SOURCE_NAMES:
                            raise ValueError('ERROR: Unknown source "' + source
                                             + '" for ' + item_name + '!')
                        self._resistance_sources[chan] = source

                    self.set_channel_names(channel_numbers=chan,
                                           channel_names=chan_name,
                                           global_channel_numbers=chan_number,
                                           device_types=device_type,
                                           device_serials=device_serial,
                                           channel_type=chan_type)
        except ValueError:
            raise
        except Exception:
            raise ValueError('ERROR: Lakeshore setup file has unknown format!')

    def connect(self):
        """
        Open connection
        """

        if str(self._model_number).upper().lstrip('M') != '81':
            raise ValueError('ERROR: This driver only supports Lakeshore M81 (model = '
                             + str(self._model_number) + ')')

        try:
            if self._ip_address is not None:
                self._inst = SSMSystem(ip_address=self._ip_address,
                                       tcp_port=(self._tcp_port if self._tcp_port is not None
                                                 else _DEFAULT_TCP_PORT))
            else:
                self._inst = SSMSystem(com_port=self._com_port,
                                       baud_rate=self._baud_rate)

        except Exception:
            print('ERROR: Unable to connect to lakeshore M81!')
            if self._raise_errors:
                raise
            else:
                return

        # properties
        self._serial_number = self._inst.serial_number
        self._firmware_version = self._inst.firmware_version

        # configure resistance sources from setup file
        for chan, source in self._resistance_sources.items():
            self.set_channel_parameters(channel_numbers=chan, resistance_source=source)

        if self._verbose:
            address = self._ip_address if self._ip_address is not None else self._com_port
            print('INFO: Lakeshore M81 (S/N ' + str(self._serial_number) + ') connected on '
                  + str(address) + '!')

    def disconnect(self):
        """
        Close connection
        """

        if self._inst is not None:
            if self._ip_address is None:
                self._inst.disconnect_usb()
            else:
                self._inst.disconnect_tcp()
            self._inst = None

        if self._verbose:
            print('INFO: Lakeshore M81 disconnected!')

    def get_modules(self):
        """
        Return dictionary of installed modules, e.g.
        {'S1': {'name':..., 'model':'BCS-10', 'serial':...}, 'M1': {...}}
        Empty slots have empty model string.
        """

        self._check_connected()

        output = dict()
        for prefix, modules in [('S', self._inst.source_modules),
                                ('M', self._inst.measure_modules)]:
            for module in modules:
                info = dict()
                try:
                    info['name'] = module.get_name()
                    info['model'] = module.get_model()
                    info['serial'] = module.get_serial()
                    output[prefix + str(module.module_number)] = info
                except XIPInstrumentException as e:
                    print(f'WARNING: Lakeshore M81 channel {str(module)} not connected, {e}')
        return output

    def set_channel_names(self, channel_numbers, channel_names,
                          global_channel_numbers=None, channel_type='resistance',
                          device_types=None, device_serials=None,
                          replace=False):
        """
        Channel name map
        """

        # convert to list if needed
        if not isinstance(channel_numbers, list):
            channel_numbers = [channel_numbers]
        nb_channels = len(channel_numbers)

        if global_channel_numbers is None:
            global_channel_numbers = channel_numbers
        elif not isinstance(global_channel_numbers, list):
            global_channel_numbers = [global_channel_numbers]

        if not isinstance(channel_names, list):
            channel_names = [channel_names]

        if device_types is None:
            device_types = [None]*nb_channels
        elif not isinstance(device_types, list):
            device_types = [device_types]

        if device_serials is None:
            device_serials = [None]*nb_channels
        elif not isinstance(device_serials, list):
            device_serials = [device_serials]

        if (len(channel_numbers) != len(channel_names) or
                len(global_channel_numbers) != len(channel_names)):
            raise ValueError('ERROR: channel numbers and names should be same length!')

        if channel_type not in ['resistance', 'heater']:
            raise ValueError('ERROR: channel type should be "resistance" or "heater"!')

        # module type: measure (M) or source (S)
        module_type = 'measure' if channel_type == 'resistance' else 'source'
        address = self._ip_address if self._ip_address is not None else self._com_port

        # select lists
        if channel_type == 'resistance':
            if self._resistance_channel_property_list is None:
                self._resistance_channel_property_list = list()
                self._resistance_channel_names = list()
                self._resistance_global_channel_numbers = list()
            property_list = self._resistance_channel_property_list
            name_list = self._resistance_channel_names
            global_list = self._resistance_global_channel_numbers
        else:
            if self._heater_channel_property_list is None:
                self._heater_channel_property_list = list()
                self._heater_channel_names = list()
                self._heater_global_channel_numbers = list()
            property_list = self._heater_channel_property_list
            name_list = self._heater_channel_names
            global_list = self._heater_global_channel_numbers

        # loop channels and add to list
        for ichan in range(nb_channels):

            channel_property_values = [self._instrument_name,
                                       'ls-' + str(self._model_number), module_type,
                                       self._instrument_number,
                                       address, channel_numbers[ichan],
                                       global_channel_numbers[ichan], channel_names[ichan],
                                       device_types[ichan], device_serials[ichan]]

            # check if channel exist (index 5 = channel_number)
            channel_index = None
            for ind in range(len(property_list)):
                if property_list[ind][5] == channel_numbers[ichan]:
                    channel_index = ind

            if channel_index is None:
                property_list.append(channel_property_values)
                name_list.append(channel_names[ichan])
                global_list.append(global_channel_numbers[ichan])
            elif replace:
                property_list[channel_index] = channel_property_values
                name_list[channel_index] = channel_names[ichan]
                global_list[channel_index] = global_channel_numbers[ichan]
            else:
                raise ValueError('ERROR: ' + channel_type.capitalize() + ' channel '
                                 + str(channel_numbers[ichan]) + ' already exists!')

        # rebuild table
        table = pd.DataFrame(property_list, columns=self._channel_properties)
        if channel_type == 'resistance':
            self._resistance_channel_table = table
        else:
            self._heater_channel_table = table

    # ------------------------------------------------------------------
    # Measure modules ("resistance" channels)
    # ------------------------------------------------------------------

    def get_channel_parameters(self, channel_numbers=None, channel_names=None,
                               global_channel_numbers=None):
        """
        Get current measure module (M1-M3) setup

        Parameters
        ----------
        channel_numbers: int or list
            1-3 = measure module number

        Return
        ----------
        setup: dictionary {channel_number: dict}
        """

        channel_numbers = self._get_channel_number_list(channel_numbers, channel_names,
                                                        global_channel_numbers, 'resistance')

        output = dict()
        for chan in channel_numbers:

            module = self._get_measure_module(chan)
            output_chan = dict()

            output_chan['module_name'] = module.get_name()
            output_chan['module_model'] = module.get_model()
            output_chan['module_serial'] = module.get_serial()
            output_chan['mode'] = module.get_mode()
            output_chan['measure_function'] = module.get_measure_function()
            output_chan['nplc'] = module.get_averaging_time()

            if self._is_voltage_function(output_chan['measure_function']):
                output_chan['range'] = module.get_voltage_range()
                output_chan['autorange'] = module.get_voltage_autorange_status()
            else:
                output_chan['range'] = module.get_current_range()
                output_chan['autorange'] = module.get_current_autorange_status()

            # resistance setup (requires source module to be configured)
            try:
                output_chan['resistance_source'] = str(module.get_resistance_source())
                output_chan['resistance_excitation_type'] = str(
                    module.get_resistance_excitation_type())
            except Exception:
                output_chan['resistance_source'] = None
                output_chan['resistance_excitation_type'] = None

            output[chan] = output_chan
            self._debug_print('M' + str(chan) + ' parameters: ' + str(output_chan))

        return output

    def set_channel_parameters(self, channel_numbers=None, channel_names=None,
                               global_channel_numbers=None,
                               mode=None, nplc=None, measure_function=None,
                               autorange=None, max_level=None,
                               resistance_source=None,
                               resistance_excitation_type=None,
                               voltage_sense_mode=None):
        """
        Setup measure module(s)

        Parameters
        ----------
        channel_numbers: int or list
           1-3 = measure module number
        mode: str (optional)
           'DC', 'AC' or 'LIA'
        nplc: float (optional)
           averaging time in power line cycles
        measure_function: str (optional)
           'voltage' or 'current' (VM-10 = voltage, CM-10 = current)
        autorange: bool (optional)
           enable/disable input autorange
        max_level: float (optional)
           maximum expected level [V or A] for manual range
        resistance_source: str (optional)
           source module used for resistance calculation: 'S1', 'S2', 'S3'
        resistance_excitation_type: str (optional)
           'DC' or 'AC'
        voltage_sense_mode: str (optional)
           'local' (2-wire) or 'remote' (4-wire)
        """

        channel_numbers = self._get_channel_number_list(channel_numbers, channel_names,
                                                        global_channel_numbers, 'resistance')

        for chan in channel_numbers:

            module = self._get_measure_module(chan)

            if measure_function is not None:
                function = 'VOLTage' if self._is_voltage_function(measure_function) else 'CURRent'
                module.set_measure_function(function)
                self._debug_print('M' + str(chan) + ' measure function = ' + function)

            if mode is not None:
                module.set_mode(mode.upper())
                self._debug_print('M' + str(chan) + ' mode = ' + mode.upper())

            if nplc is not None:
                module.set_averaging_time(nplc)
                self._debug_print('M' + str(chan) + ' nplc = ' + str(nplc))

            if autorange is not None or max_level is not None:
                if autorange is None:
                    autorange = False
                if not autorange and max_level is None:
                    raise ValueError('ERROR: "max_level" required for manual range!')
                if self._is_voltage_function(module.get_measure_function()):
                    module.configure_voltage_range(autorange, max_level=max_level)
                else:
                    module.configure_current_range(autorange, max_level=max_level)
                self._debug_print('M' + str(chan) + ' range: autorange=' + str(autorange)
                                  + ', max_level=' + str(max_level))

            if voltage_sense_mode is not None:
                sense = 'REMote' if voltage_sense_mode.lower().startswith('rem') else 'LOCal'
                module.set_voltage_sense_mode(sense)
                self._debug_print('M' + str(chan) + ' voltage sense = ' + sense)

            if resistance_source is not None:
                source = str(resistance_source).strip().upper()
                if source not in _SOURCE_NAMES:
                    raise ValueError('ERROR: resistance source should be S1, S2 or S3!')
                module.set_resistance_source(source)
                self._resistance_sources[chan] = source
                self._debug_print('M' + str(chan) + ' resistance source = ' + source)

            if resistance_excitation_type is not None:
                module.set_resistance_excitation_type(resistance_excitation_type.upper())
                self._debug_print('M' + str(chan) + ' resistance excitation = '
                                  + resistance_excitation_type.upper())

    def get_measurement(self, channel_number=None, channel_name=None,
                        global_channel_number=None, quantity='dc'):
        """
        Get measure module reading in module units (V or A)

        quantity: str
           'dc', 'rms', 'peak_to_peak', 'lock_in_x', 'lock_in_y',
           'lock_in_r', 'lock_in_theta'
        """

        channel_number = self._get_single_channel_number(channel_number, channel_name,
                                                         global_channel_number, 'resistance')
        module = self._get_measure_module(channel_number)

        functions = {'dc': module.get_dc,
                     'rms': module.get_rms,
                     'peak_to_peak': module.get_peak_to_peak,
                     'lock_in_x': module.get_lock_in_x,
                     'lock_in_y': module.get_lock_in_y,
                     'lock_in_r': module.get_lock_in_r,
                     'lock_in_theta': module.get_lock_in_theta}

        if quantity not in functions:
            raise ValueError('ERROR: quantity should be one of ' + str(list(functions.keys())))

        value = functions[quantity]()
        self._debug_print('M' + str(channel_number) + ' ' + quantity + ' = ' + str(value))
        return value

    def get_resistance(self, channel_number=None, channel_name=None,
                       global_channel_number=None):
        """
        Get resistance [Ohms] computed by the M81 from the measure module
        and its paired source module (see "resistance_source")
        """

        channel_number = self._get_single_channel_number(channel_number, channel_name,
                                                         global_channel_number, 'resistance')
        module = self._get_measure_module(channel_number)

        # configure resistance source if known but not yet set
        if channel_number in self._resistance_sources:
            try:
                current_source = str(module.get_resistance_source())
            except Exception:
                current_source = None
            if current_source != self._resistance_sources[channel_number]:
                module.set_resistance_source(self._resistance_sources[channel_number])

        try:
            resistance = module.get_resistance()
        except Exception:
            print('ERROR: Unable to read resistance on M' + str(channel_number)
                  + '. Make sure a source module is configured '
                  + '(set_channel_parameters(resistance_source="S1")) and enabled.')
            raise

        self._debug_print('M' + str(channel_number) + ' resistance = ' + str(resistance))
        return resistance

    def get_temperature(self, channel_number=None, channel_name=None,
                        global_channel_number=None,
                        manual_conversion=False):
        """
        Not available: the M81 has no thermometer calibration curves
        """

        raise NotImplementedError('ERROR: Lakeshore M81 has no temperature calibration curves. '
                                  + 'Use get_resistance() and convert offline.')

    # ------------------------------------------------------------------
    # Source modules ("heater" channels)
    # ------------------------------------------------------------------

    def get_heater_parameters(self,
                              heater_channel_numbers=None,
                              heater_channel_names=None,
                              heater_global_channel_numbers=None):
        """
        Get source module (S1-S3) parameters
        """

        heater_channel_numbers = self._get_channel_number_list(
            heater_channel_numbers, heater_channel_names,
            heater_global_channel_numbers, 'heater')

        output = dict()
        for chan in heater_channel_numbers:

            module = self._get_source_module(chan)
            output_chan = dict()

            output_chan['module_name'] = module.get_name()
            output_chan['module_model'] = module.get_model()
            output_chan['module_serial'] = module.get_serial()
            output_chan['enabled'] = module.get_enable_state()

            function = str(module.get_source_function())
            output_chan['heater_mode'] = ('voltage' if self._is_voltage_function(function)
                                          else 'current')
            output_chan['shape'] = module.get_shape()
            output_chan['frequency'] = module.get_frequency()

            if output_chan['heater_mode'] == 'current':
                output_chan['amplitude'] = module.get_current_amplitude()
                output_chan['offset'] = module.get_current_offset()
                output_chan['range'] = module.get_current_range()
                output_chan['autorange'] = module.get_current_autorange_status()
                output_chan['limit'] = module.get_current_limit()
            else:
                output_chan['amplitude'] = module.get_voltage_amplitude()
                output_chan['offset'] = module.get_voltage_offset()
                output_chan['range'] = module.get_voltage_range()
                output_chan['autorange'] = module.get_voltage_autorange_status()
                output_chan['limit'] = module.get_voltage_limit()

            output[chan] = output_chan
            self._debug_print('S' + str(chan) + ' parameters: ' + str(output_chan))

        return output

    def set_heater_parameters(self,
                              heater_channel_numbers=None,
                              heater_channel_names=None,
                              heater_global_channel_numbers=None,
                              heater_mode=None,
                              autorange=None,
                              max_level=None,
                              current_limit=None,
                              voltage_limit=None):
        """
        Set source module parameters

        heater_mode: str
           'current' or 'voltage'
        autorange: bool
           source autorange
        max_level: float
           largest level to be sourced [A or V] (for manual range)
        current_limit / voltage_limit: float
           compliance limits [A] / [V]
        """

        heater_channel_numbers = self._get_channel_number_list(
            heater_channel_numbers, heater_channel_names,
            heater_global_channel_numbers, 'heater')

        for chan in heater_channel_numbers:

            module = self._get_source_module(chan)

            if heater_mode is not None:
                function = 'VOLTage' if self._is_voltage_function(heater_mode) else 'CURRent'
                module.set_source_function(function)
                self._debug_print('S' + str(chan) + ' function = ' + function)

            if autorange is not None or max_level is not None:
                if autorange is None:
                    autorange = False
                if not autorange and max_level is None:
                    raise ValueError('ERROR: "max_level" required for manual range!')
                if self._is_voltage_function(str(module.get_source_function())):
                    module.configure_voltage_range(autorange, max_level=max_level)
                else:
                    module.configure_current_range(autorange, max_level=max_level)
                self._debug_print('S' + str(chan) + ' range: autorange=' + str(autorange)
                                  + ', max_level=' + str(max_level))

            if current_limit is not None:
                module.set_current_limit(current_limit)
                self._debug_print('S' + str(chan) + ' current limit = ' + str(current_limit))

            if voltage_limit is not None:
                module.set_voltage_limit(voltage_limit)
                self._debug_print('S' + str(chan) + ' voltage limit = ' + str(voltage_limit))

    def set_heater(self,
                   heater_channel_numbers=None,
                   heater_channel_names=None,
                   heater_global_channel_numbers=None,
                   on=None,
                   heater_mode=None,
                   heater_current=None,
                   heater_voltage=None,
                   heater_range=None):
        """
        Set source module output (DC)

        on: bool
           True/False = enable/disable output
        heater_mode: str
           'current' or 'voltage' (default: present mode)
        heater_current: float
           DC current [A] (current mode)
        heater_voltage: float
           DC voltage [V] (voltage mode)
        heater_range: float
           largest level [A or V] for manual range (None = no change)
        """

        heater_channel_numbers = self._get_channel_number_list(
            heater_channel_numbers, heater_channel_names,
            heater_global_channel_numbers, 'heater')

        if heater_current is not None and heater_voltage is not None:
            raise ValueError('ERROR: Provide either "heater_current" or "heater_voltage"!')

        if heater_current is not None:
            heater_mode = 'current'
        elif heater_voltage is not None:
            heater_mode = 'voltage'

        for chan in heater_channel_numbers:

            module = self._get_source_module(chan)

            # turn off
            if on is not None and not on:
                if self._verbose:
                    print('INFO: Turning off source S' + str(chan))
                module.disable()
                continue

            # mode
            if heater_mode is None:
                mode = ('voltage' if self._is_voltage_function(str(module.get_source_function()))
                        else 'current')
            else:
                mode = 'voltage' if self._is_voltage_function(heater_mode) else 'current'

            # range
            if heater_range is not None:
                if mode == 'current':
                    module.configure_current_range(False, max_level=heater_range)
                else:
                    module.configure_voltage_range(False, max_level=heater_range)
                self._debug_print('S' + str(chan) + ' range = ' + str(heater_range))

            # level (apply_dc_* sets function and DC shape)
            output_enable = bool(on) if on is not None else module.get_enable_state()
            if heater_current is not None:
                module.apply_dc_current(heater_current, output_enable=output_enable)
                self._debug_print('S' + str(chan) + ' DC current = ' + str(heater_current))
            elif heater_voltage is not None:
                module.apply_dc_voltage(heater_voltage, output_enable=output_enable)
                self._debug_print('S' + str(chan) + ' DC voltage = ' + str(heater_voltage))
            elif heater_mode is not None:
                module.set_source_function('VOLTage' if mode == 'voltage' else 'CURRent')

            # turn on (apply_dc_* already enabled the output)
            if on and heater_current is None and heater_voltage is None:
                if self._verbose:
                    print('INFO: Turning on source S' + str(chan))
                module.enable()

    # ------------------------------------------------------------------
    # Functions not available on M81
    # ------------------------------------------------------------------

    def get_pid_control(self, *args, **kwargs):
        raise NotImplementedError('ERROR: Lakeshore M81 has no PID control!')

    def set_pid_control(self, *args, **kwargs):
        raise NotImplementedError('ERROR: Lakeshore M81 has no PID control!')

    def set_temperature(self, *args, **kwargs):
        raise NotImplementedError('ERROR: Lakeshore M81 has no PID temperature control!')

    def get_scan_parameters(self):
        raise NotImplementedError('ERROR: Lakeshore M81 has no scanner!')

    def select_channel(self, *args, **kwargs):
        raise NotImplementedError('ERROR: Lakeshore M81 has no scanner!')

    def start_scan(self, *args, **kwargs):
        raise NotImplementedError('ERROR: Lakeshore M81 has no scanner!')

    def stop_scan(self, *args, **kwargs):
        raise NotImplementedError('ERROR: Lakeshore M81 has no scanner!')

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_connected(self):
        if self._inst is None:
            raise ValueError('ERROR: Lakeshore M81 not connected! Use connect() first.')

    def _get_measure_module(self, channel_number):
        self._check_connected()
        channel_number = int(channel_number)
        if channel_number < 1 or channel_number > len(self._inst.measure_modules):
            raise ValueError('ERROR: measure channel number should be between 1-'
                             + str(len(self._inst.measure_modules)) + '!')
        return self._inst.get_measure_module(channel_number)

    def _get_source_module(self, channel_number):
        self._check_connected()
        channel_number = int(channel_number)
        if channel_number < 1 or channel_number > len(self._inst.source_modules):
            raise ValueError('ERROR: source (heater) channel number should be between 1-'
                             + str(len(self._inst.source_modules)) + '!')
        return self._inst.get_source_module(channel_number)

    @staticmethod
    def _is_voltage_function(function):
        return str(function).strip().upper().startswith('VOLT')

    def _debug_print(self, msg):
        if self._debug:
            print('DEBUG: ' + msg)

    def _get_channel_number_list(self, channel_numbers, channel_names,
                                 global_channel_numbers, channel_type):
        if channel_names is not None or global_channel_numbers is not None:
            channel_numbers = self._extract_channel_numbers(
                channel_names=channel_names,
                global_channel_numbers=global_channel_numbers,
                channel_type=channel_type)
        if channel_numbers is None:
            raise ValueError('ERROR: channel number, name or global number required!')
        if not isinstance(channel_numbers, list):
            channel_numbers = [channel_numbers]
        return [int(chan) for chan in channel_numbers]

    def _get_single_channel_number(self, channel_number, channel_name,
                                   global_channel_number, channel_type):
        channel_numbers = self._get_channel_number_list(channel_number, channel_name,
                                                        global_channel_number, channel_type)
        if len(channel_numbers) != 1:
            raise ValueError('ERROR: Unable to extract channel number!')
        return channel_numbers[0]

    def _extract_channel_numbers(self, channel_names=None,
                                 global_channel_numbers=None,
                                 channel_type='resistance'):
        """
        Get channel numbers
        """

        channel_table = self._resistance_channel_table
        if channel_type == 'heater':
            channel_table = self._heater_channel_table

        if channel_table is None:
            raise ValueError('ERROR: No ' + channel_type + ' channels defined!')

        channel_numbers = list()
        if channel_names is not None:

            if not isinstance(channel_names, list):
                channel_names = [channel_names]

            for chan in channel_names:
                channel_number = channel_table.query('channel_name == @chan')['channel_number'].values
                if len(channel_number) > 1:
                    raise ValueError('ERROR: Multiple channel numbers found for "' + chan + '"!')
                elif len(channel_number) == 1:
                    channel_numbers.append(int(channel_number[0]))
                else:
                    raise ValueError('ERROR: No channel found for "' + chan + '"!')

        elif global_channel_numbers is not None:

            if not isinstance(global_channel_numbers, list):
                global_channel_numbers = [global_channel_numbers]

            for chan in global_channel_numbers:
                channel_number = channel_table.query(
                    'global_channel_number == @chan')['channel_number'].values
                if len(channel_number) > 1:
                    raise ValueError('ERROR: Multiple channel numbers found for global channel number "'
                                     + str(chan) + '"!')
                elif len(channel_number) == 1:
                    channel_numbers.append(int(channel_number[0]))
                else:
                    raise ValueError('ERROR: No channel found for global channel number "'
                                     + str(chan) + '"!')

        return channel_numbers
