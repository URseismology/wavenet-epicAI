"""Reproduce the ObsPy MassDownloader TypeError with a FULL traceback, to find where it is
actually raised. Known signature: 'sequence item 0: expected str instance, tuple found'."""
import os, shutil, sys, traceback, logging
from obspy import UTCDateTime
from obspy.clients.fdsn.mass_downloader import Restrictions, MassDownloader, RectangularDomain

net, sta, lat, lon, year = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
base = f"/scratch/tolugboj_lab/wavenet_ncf_migration/repro_{net}_{sta}_{year}"
shutil.rmtree(base, ignore_errors=True)
md, xd = base+"/mseed", base+"/xml"
os.makedirs(md); os.makedirs(xd)
logging.getLogger("obspy.clients.fdsn.mass_downloader").setLevel(logging.ERROR)

domain = RectangularDomain(minlatitude=lat-0.5, maxlatitude=lat+0.5,
                            minlongitude=lon-0.5, maxlongitude=lon+0.5)
r = Restrictions(starttime=UTCDateTime(year,1,1), endtime=UTCDateTime(year,12,31,23,59,59),
                 chunklength_in_sec=86400, network=net, station=sta,
                 channel="BH?,LH?", reject_channels_with_gaps=False, minimum_length=0.0,
                 channel_priorities=["LH?","BH?"], location_priorities=["","00","10"])
try:
    MassDownloader().download(domain, r, mseed_storage=md, stationxml_storage=xd)
    print(f"NO_ERROR mseed={len(os.listdir(md))} xml={len(os.listdir(xd))}")
except Exception:
    print("REPRODUCED. Full traceback:")
    traceback.print_exc()
    print(f"state at failure: mseed={len(os.listdir(md))} xml={len(os.listdir(xd))}")
