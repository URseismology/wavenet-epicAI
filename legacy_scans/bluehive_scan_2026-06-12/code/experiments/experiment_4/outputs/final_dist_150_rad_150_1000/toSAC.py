import numpy as np
from obspy import Trace, Stream
from obspy.core import UTCDateTime

# Load your CCF
data = np.loadtxt('stacked_time_ccf.txt')
lags = data[:, 0]  # lag times in seconds
ccf = data[:, 1]   # CCF amplitudes

# Create SAC trace
tr = Trace(data=ccf)
tr.stats.delta = 0.5  # Your DELTA from compute_ccf_final.py
tr.stats.station = 'R2'
tr.stats.channel = 'BHZ'
tr.stats.network = 'WN'  # WaveNET
tr.stats.starttime = UTCDateTime(0)  # Arbitrary reference

# Add SAC-specific headers
tr.stats.sac = {}
tr.stats.sac.dist = 150000  # Distance in METERS (150 km)
tr.stats.sac.b = lags[0]    # Begin time (first lag)

# Save as SAC
tr.write('stacked_ccf_150km.sac', format='SAC')
print(f"Created SAC file with {len(ccf)} samples, dt={tr.stats.delta}s")
