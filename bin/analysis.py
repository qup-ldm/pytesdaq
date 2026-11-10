import qetpy as qp
import numpy as np
import pandas as pd
from pprint import pprint
import matplotlib.pyplot as plt
import pickle



# detprocess
from detprocess import IVSweepAnalysis, IVSweepProcessing

channel_name = 'TES1'
base_path = 'run16'

melange1pc_ivsweep_path = base_path + '/raw/iv_didv_I5_D20260906_T104839'
myproc = IVSweepProcessing(melange1pc_ivsweep_path)
