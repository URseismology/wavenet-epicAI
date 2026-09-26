"""Unit tests for the two behaviours added to rover_download/build_master_h5.py by the XD.MTAN/XD.RUNG smoke-test fixes.

Run from the repo root (pytest or plain python):
    pytest docs/ncf_pipeline_stages/xd_mtan_rung_smoke_test/tests/test_patches.py
    python  docs/ncf_pipeline_stages/xd_mtan_rung_smoke_test/tests/test_patches.py
By default the tests import the PATCHED copy shipped in ../patches/patched_files/rover_download/ ; set
BUILD_MASTER_DIR=<dir containing build_master_h5.py> to test any other copy (e.g. the repo's own file after you apply the diff).
Each test states the failure it guards against (see PATCHES.md for the evidence behind each fix).
"""
import os, sys, tempfile
import numpy as np
import h5py
from obspy import Trace, UTCDateTime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("BUILD_MASTER_DIR", os.path.join(HERE, "..", "patches", "patched_files", "rover_download")))
from build_master_h5 import align_to_integer_second, append_channel_data  # noqa: E402


def _signal(t):
    """Smooth multi-tone, every component below the pipeline's 0.4 Hz low-pass (so a fractional delay is exact)."""
    return sum(a * np.sin(2 * np.pi * f * t + p)
               for a, f, p in [(1, 0.013, 0.3), (0.7, 0.041, 1.1), (0.5, 0.12, 2.0), (0.3, 0.31, 0.5), (0.2, 0.0071, 4.0)])


def test_align_puts_sample0_on_integer_second_without_distorting_the_signal():
    """PATCH 1. Failure guarded: day-start sub-second phase (any of k*0.05 s for 20 Hz data) leaving data mis-timed by up to 1 s."""
    n = 86400
    for frac in (0.0221, 0.05, 0.3, 0.701, 0.95, 0.999, -0.4):
        t_start = 1e9 + frac
        tr = Trace(data=_signal(t_start + np.arange(n)), header=dict(delta=1.0, starttime=UTCDateTime(t_start)))
        align_to_integer_second(tr)
        assert tr.stats.starttime.timestamp % 1 == 0
        exact = _signal(tr.stats.starttime.timestamp + np.arange(n))
        assert np.abs(tr.data - exact)[7200:-7200].max() < 1e-4     # measured ~1e-6 (signal amplitude ~2.7)


def _grp(tmp):
    f = h5py.File(os.path.join(tmp, "t.h5"), "w")
    return f, f.create_group("XD.TST")


def _append(g, start, n, ch="BHZ"):
    return append_channel_data(g, ch, np.ones(n, dtype=np.float32), 1.0, start, "m")


def test_contiguous_days_append_with_no_gap():
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d); T0 = UTCDateTime(1994, 5, 26)
        assert _append(g, T0, 86400) == "created"
        assert _append(g, T0 + 86400, 86400).startswith("appended (86,400 samples, gap=0")
        assert len(g["BHZ"]) == 172800
        f.close()


def test_one_extra_boundary_sample_is_trimmed_not_refused():
    """PATCH 1b. Failure guarded: a day file with 1,728,001 raw samples decimates to 86,401, its last sample duplicates the next
    day's first, and the ORIGINAL code refused the ENTIRE next day (6 of 318 station-days on XD.MTAN/XD.RUNG)."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d); T0 = UTCDateTime(1994, 5, 26)
        _append(g, T0, 86401)                                    # 86,401 samples: last one sits on the next midnight
        st = _append(g, T0 + 86400, 86400)                        # next day starts at that same second
        assert st.startswith("appended") and "trimmed 1 overlapping" in st
        assert len(g["BHZ"]) == 86401 + 86399
        f.close()


def test_real_overlap_is_still_refused():
    """Guards the safety net: genuinely duplicated data (> max_trim_samples) must not be silently merged.

    Amended 2026-09-25 (production migration): this originally asserted a 10-sample overlap is REFUSED, which
    silently encoded the then-default max_trim_samples=2. Production raised that default to 60, because every
    real refusal observed at scale was a 10-19 sample day-boundary artefact (a day file starting seconds before
    midnight while the previous day ran seconds past it), and refusing them cost 18% of all attempted days --
    NL.HGN alone lost 490 of 572. The original assertion is preserved verbatim by passing max_trim_samples=2
    explicitly, so this tests the BEHAVIOUR rather than one particular default; a second case covers the
    current default."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d); T0 = UTCDateTime(1994, 5, 26)
        _append(g, T0, 86400)
        # original assertion, now pinned to the threshold it was written against
        assert append_channel_data(g, "BHZ", np.ones(86400, dtype=np.float32), 1.0,
                                    T0 + 86400 - 10, "m", max_trim_samples=2).startswith("REFUSED")
        assert len(g["BHZ"]) == 86400
        # and an overlap well beyond the current production default is still refused
        assert _append(g, T0 + 86400 - 5000, 86400).startswith("REFUSED")
        assert len(g["BHZ"]) == 86400
        f.close()


def test_reappend_of_stored_data_is_skipped_not_refused():
    """A resubmitted task re-appending a day already stored is idempotent (SKIPPED), so PATCH 2 (do not mark refused days done)
    cannot leave a day stuck forever."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d); T0 = UTCDateTime(1994, 5, 26)
        _append(g, T0, 86400); _append(g, T0 + 86400, 86400)
        st = _append(g, T0, 86400)
        assert st.startswith("SKIPPED") and len(g["BHZ"]) == 172800
        f.close()


def test_real_gap_is_zero_filled():
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d); T0 = UTCDateTime(1994, 5, 26)
        _append(g, T0, 86400)
        st = _append(g, T0 + 86400 + 3, 10)                       # 3 s late -> 3 missing samples
        assert "gap=3" in st and len(g["BHZ"]) == 86400 + 3 + 10
        assert np.all(g["BHZ"][86400:86403] == 0)
        f.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("PASS", name)
