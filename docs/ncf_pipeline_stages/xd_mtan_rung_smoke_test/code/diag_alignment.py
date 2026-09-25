"""RUNG packaged slice vs fresh replay: same rms but not identical -> find the sample lag and the placement arithmetic."""
import glob, os
import h5py, numpy as np
from obspy import UTCDateTime, read, read_inventory
R = "/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test"
def replay(tr, inv):
    tr.detrend("linear"); tr.detrend("demean")
    tr.remove_response(inventory=inv, output="DISP", water_level=60, pre_filt=(0.001, 0.005, 0.4, 0.5))
    tr.filter("lowpass", freq=0.4, corners=4, zerophase=True); tr.filter("highpass", freq=1.0/3600.0, corners=4, zerophase=True)
    tr.decimate(factor=int(round(tr.stats.sampling_rate)), no_filter=True); tr.detrend("demean"); tr.taper(max_percentage=0.05)
    return tr
for sta, day in [("MTAN","19940526"),("RUNG","19940526"),("RUNG","19940530"),("MTAN","19940530")]:
    f0 = sorted(glob.glob(f"{R}/scratch_work/XD_{sta}/mseed/*BHZ__{day}T*"))[0]
    tr = replay(read(f0)[0], read_inventory(f"{R}/scratch_work/XD_{sta}/stationxml/XD.{sta}.xml"))
    fresh = tr.data.astype(np.float32).astype(np.float64)
    with h5py.File(f"{R}/packaged_h5/XD.{sta}.h5","r") as f:
        ds = f[f"XD.{sta}"]["BHZ"]; t0 = UTCDateTime(ds.attrs["start_time"]); n=len(ds)
        raw_off = tr.stats.starttime - t0
        i_nearest = int(round(raw_off))
        best=None
        for lag in range(-3,4):
            i0 = i_nearest + lag
            pk = ds[i0:i0+len(fresh)].astype(np.float64)
            m = min(len(pk), len(fresh))
            d = float(np.max(np.abs(pk[:m]-fresh[:m]))/(np.max(np.abs(fresh[:m]))+1e-30))
            if best is None or d < best[1]: best=(lag,d)
    print(f"XD.{sta} {day}: fresh start={tr.stats.starttime} dataset t0={t0} (start-t0)={raw_off:.4f} s -> nearest idx {i_nearest}; "
          f"best matching lag = {best[0]:+d} samples (rel diff {best[1]:.2e})", flush=True)
