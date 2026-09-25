import sys, tempfile, os, numpy as np, h5py
sys.path.insert(0, os.environ["BM"])
from build_master_h5 import append_channel_data
from obspy import UTCDateTime
T0 = UTCDateTime(1994, 5, 26)
with tempfile.TemporaryDirectory() as d:
    f = h5py.File(os.path.join(d, "t.h5"), "w"); g = f.create_group("XD.TST")
    print("day 1 (86,401 samples):     ", append_channel_data(g, "BHZ", np.ones(86401, np.float32), 1.0, T0, "m"))
    print("day 2 (starts on same slot):", append_channel_data(g, "BHZ", np.ones(86400, np.float32), 1.0, T0 + 86400, "m"))
    print("dataset length:", len(g["BHZ"]), "(day 2 lost)" if len(g["BHZ"]) == 86401 else "")
    # placement with a sub-second start phase: where does day 2 land relative to its true time?
    g2 = f.create_group("XD.TS2")
    append_channel_data(g2, "BHZ", np.ones(52424, np.float32), 1.0, UTCDateTime(1994, 5, 25, 9, 26, 16.701), "m")
    st = append_channel_data(g2, "BHZ", np.ones(86400, np.float32), 1.0, UTCDateTime(1994, 5, 26, 0, 0, 0.001), "m")
    d1_end = UTCDateTime(1994, 5, 25, 9, 26, 16.701) + 52423
    placed = UTCDateTime(1994, 5, 25, 9, 26, 16.701) + len(g2["BHZ"]) - 86400
    print("partial first day (start .701 s) then a day whose true start is 00:00:00.001:")
    print("   grid time assigned to day 2's first sample:", placed, "-> error vs true start = %+.3f s" % (placed - UTCDateTime(1994, 5, 26, 0, 0, 0.001)))
