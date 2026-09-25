from obspy import read_inventory
import glob
for sta in ("MTAN","RUNG"):
    inv = read_inventory(glob.glob(f"/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test/scratch_work/XD_{sta}/stationxml/*.xml")[0])
    ch = [c for c in inv[0][0] if c.code=="BHZ"][0]; r = ch.response; s = r.instrument_sensitivity
    print(f"XD.{sta} BHZ  epoch {ch.start_date} -> {ch.end_date}: sensitivity {s.value} counts per {s.input_units} at {s.frequency} Hz; stage1 input {r.response_stages[0].input_units}, gain {r.response_stages[0].stage_gain}")
