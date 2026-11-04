#!/bin/bash
# continuous data
#python3 run_daq.py -c TES1 TES2 TES4  \
#	   --acquire-cont \
#	   --duration 1m \
#       --comment "Laser pulses TES2, TES1 and TES4 unbiased" #\
#       --quiet

#IV
#python3 run_iv_didv.py -c TES2 \
#	   --enable-didv \
#       --comment "TES2 DIDV measurements at 10mK" #\
#dIdV
python3 run_iv_didv.py -c TES4 \
       --enable-iv --enable-didv \
       --comment "TES4 IV and dIdV measurements at 10mK" #\

