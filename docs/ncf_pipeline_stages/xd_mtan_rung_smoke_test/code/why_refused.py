import glob, json, os, math
from obspy import read, UTCDateTime
R="/scratch/tolugboj_lab/wavenet_ncf_xd_pair_test"
rep=json.load(open(f"{R}/results/placement_errors.json"))
def info(sta, day):
    f=sorted(glob.glob(f"{R}/scratch_work/XD_{sta}/mseed/*BHZ__{day}T*"))
    if not f: return None
    st=read(f[0], headonly=True)
    s=min(t.stats.starttime for t in st); e=max(t.stats.endtime for t in st); fs=st[0].stats.sampling_rate
    return dict(start=str(s.time), end=str(e.time), end_date=str(e.date), n_raw=int(round((e-s)*fs))+1, segs=len(st), n_dec=math.ceil((int(round((e-s)*fs))+1)/20))
for sta in ("MTAN","RUNG"):
    days=sorted(rep[sta]["days"])
    print(f"=== {sta}: refused {rep[sta]['refused']}")
    for rd in rep[sta]["refused"]:
        prev=UTCDateTime(rd)-86400; pd_=prev.strftime("%Y%m%d")
        print(f"  refused {rd}: this day {info(sta, rd)}")
        print(f"     previous day {pd_}: {info(sta, pd_)}   placed idx/n/e: {rep[sta]['days'].get(pd_)}")
# how many days have the inclusive-endpoint sample (n_raw = 1728001) or end past midnight, per station
for sta in ("MTAN","RUNG"):
    cnt={}
    for f in sorted(glob.glob(f"{R}/scratch_work/XD_{sta}/mseed/*BHZ__*")):
        st=read(f, headonly=True); s=min(t.stats.starttime for t in st); e=max(t.stats.endtime for t in st)
        n=int(round((e-s)*st[0].stats.sampling_rate))+1
        cnt[n]=cnt.get(n,0)+1
    print(sta, "raw sample counts per day file (n_raw: number of days):", dict(sorted(cnt.items(), key=lambda kv:-kv[1])[:6]))
