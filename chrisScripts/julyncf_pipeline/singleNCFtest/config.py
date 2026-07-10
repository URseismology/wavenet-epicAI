#!/usr/bin/env python
"""

This file is the configuration file that sets the parameters throughout the cross correlation
pipeline for the two station pairs

It is split into three parts: 

1 - The EarthScope bucket in which we wish to access 
2 - The default configuration for cross correlating
3 - Function to build the cross correlation through Noisepy 

"""

from datetime import datetime, timezone
from noisepy.seis.io.datatypes import (
    ConfigParameters, StackMethod, CCMethod,
    FreqNorm, RmResp, TimeNorm
)

EARTHSCOPE_BUCKET = "earthscope-mseed-res-na3mtd4fq5kz7pntcyr1uh46use2a--ol-s3"

CONFIG = {
    # Parameters for cross correlation and other parts of framework
    "sampling_rate": 1.0,
    "freqmin": 1.0 / 60.0,
    "freqmax": 1.0 / 3.0,
    "cc_len": 14400,
    "step": 7200,
    "maxlag": 7200,
    "ncomp": 3,
    "channels": ["LH?", "BH?", "HH?"],
    "taper_fraction": 0.05,
    "water_level": 60,
    "max_over_std": 10,
    "chunk_days": 30,
}


def build_config(start_date: datetime, end_date: datetime) -> ConfigParameters:
    """Constructs the configuration in a way that Noisepy can read"""
    config = ConfigParameters()

    config.start_date = start_date
    config.end_date = end_date

    config.sampling_rate = CONFIG["sampling_rate"]
    config.cc_len = CONFIG["cc_len"]
    config.step = CONFIG["step"]
    config.inc_hours = 24
    config.ncomp = CONFIG["ncomp"]

    config.acorr_only = False
    config.xcorr_only = True

    config.stationxml = False
    config.rm_resp = RmResp.NO
    config.freqmin = CONFIG["freqmin"]
    config.freqmax = 0.45
    config.max_over_std = CONFIG["max_over_std"]

    config.freq_norm = FreqNorm.RMA
    config.time_norm = TimeNorm.RMA

    config.cc_method = CCMethod.XCORR
    config.stack_method = StackMethod.ALL

    config.substack = False
    config.substack_windows = 1
    config.maxlag = CONFIG["maxlag"]

    config.channels = CONFIG["channels"]

    return config
