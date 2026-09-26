"""Schema v2 (absolute-time grid) -- the properties the PI made requirements on 2026-09-26:
grow forward OR backward without raw, O(1) insert, and efficient time lookup/load for
pairwise correlation. Each test names the failure it guards against.

Run:  BUILD_MASTER_DIR=<dir with build_master_h5.py> pytest test_schema_v2.py
"""
import os, sys, tempfile
import numpy as np
import h5py
from obspy import UTCDateTime

sys.path.insert(0, os.environ.get(
    "BUILD_MASTER_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rover_download")))
from build_master_h5 import (EPOCH, write_channel_day, absolute_index,  # noqa: E402
                              covered_days, merge_channel_v2)

DAY = 86400


def _grp(tmp, name="t.h5"):
    f = h5py.File(os.path.join(tmp, name), "w")
    return f, f.create_group("XX.TEST")


def _day(g, day_utc, value=1.0, n=DAY):
    return write_channel_day(g, "BHZ", np.full(n, value, dtype=np.float32), 1.0, day_utc, "m")


def test_backward_insert_is_possible_at_all():
    """THE requirement. Schema v1 could only append, so a 1993 day could never be added to a
    shard already holding 2001 (NL.HGN's real case) without rebuilding from raw."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d)
        _day(g, UTCDateTime(2001, 6, 6), 2.0)
        st = _day(g, UTCDateTime(1993, 11, 3), 1.0)          # eight years EARLIER
        assert st.startswith("written"), st
        i_old = absolute_index(UTCDateTime(1993, 11, 3), 1.0)
        i_new = absolute_index(UTCDateTime(2001, 6, 6), 1.0)
        assert g["BHZ"][i_old] == 1.0 and g["BHZ"][i_new] == 2.0
        f.close()


def test_insert_is_order_independent():
    """Same days written in any order must give a byte-identical result -- that is what
    makes 'grow later' safe rather than order-sensitive."""
    days = [UTCDateTime(2005, 1, d) for d in (1, 2, 3, 4, 5)]
    out = []
    for order in (days, list(reversed(days)), [days[2], days[0], days[4], days[1], days[3]]):
        with tempfile.TemporaryDirectory() as d:
            f, g = _grp(d)
            for i, day in enumerate(order):
                _day(g, day, float(days.index(day) + 1))
            out.append(g["BHZ"][()].copy())
            f.close()
    assert np.array_equal(out[0], out[1]) and np.array_equal(out[0], out[2])


def test_absolute_time_lookup_is_exact_and_shared():
    """Two stations must land on ONE grid so a correlation window is the same slice index
    in both -- no offset table, no resampling. This is the pairwise-load requirement."""
    with tempfile.TemporaryDirectory() as d:
        f = h5py.File(os.path.join(d, "t.h5"), "w")
        ga, gb = f.create_group("XX.A"), f.create_group("XX.B")
        day = UTCDateTime(2010, 3, 14)
        write_channel_day(ga, "BHZ", np.arange(DAY, dtype=np.float32), 1.0, day, "m")
        write_channel_day(gb, "BHZ", np.arange(DAY, dtype=np.float32) * 2, 1.0, day, "m")
        t0 = day + 3600
        i0 = absolute_index(t0, 1.0)
        a, b = ga["BHZ"][i0:i0 + 600], gb["BHZ"][i0:i0 + 600]
        assert np.allclose(b, a * 2)                       # same window, same indices
        assert (EPOCH + i0) == t0                          # index math is exact
        f.close()


def test_every_sample_time_is_exact_by_construction():
    """Kills schema v1's entire timing-bug class: there is no anchor phase to drift."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d)
        for day in (UTCDateTime(1999, 1, 1), UTCDateTime(2015, 7, 9)):
            _day(g, day)
            i = absolute_index(day, 1.0)
            assert (EPOCH + i) == day
            assert (EPOCH + i).timestamp % 1 == 0
        f.close()


def test_coverage_distinguishes_no_data_from_quiet_data():
    """Zeros are ambiguous on a sparse grid. Without this a consumer would correlate
    never-written span as if it were real quiet ground motion."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d)
        _day(g, UTCDateTime(2005, 1, 1), 5.0)
        _day(g, UTCDateTime(2005, 1, 5), 5.0)               # 3-day hole between
        cov = covered_days(g, "BHZ")
        d0 = int((UTCDateTime(2005, 1, 1) - EPOCH) // DAY)
        assert d0 in cov and d0 + 4 in cov
        assert d0 + 1 not in cov and d0 + 3 not in cov      # the hole is explicit
        f.close()


def test_sparse_storage_costs_only_what_was_written():
    """A 2005 day in a 1970-anchored dataset must not allocate 35 years of zeros."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d)
        _day(g, UTCDateTime(2005, 1, 1), 1.0)
        f.close()
        on_disk = os.path.getsize(os.path.join(d, "t.h5"))
        logical = absolute_index(UTCDateTime(2005, 1, 2), 1.0) * 4      # float32
        assert on_disk < logical / 100, (on_disk, logical)


def test_overlapping_day_is_an_idempotent_overwrite_not_an_error():
    """Under v1 an overlap was REFUSED and the day lost (18% of days at one point). On an
    absolute grid the same instant is simply the same slot."""
    with tempfile.TemporaryDirectory() as d:
        f, g = _grp(d)
        day = UTCDateTime(2008, 2, 16)
        _day(g, day, 1.0)
        st = _day(g, day, 1.0)
        assert st.startswith("written"), st
        i = absolute_index(day, 1.0)
        assert np.all(g["BHZ"][i:i + DAY] == 1.0)
        f.close()


def test_merge_preserves_absolute_position_and_coverage():
    with tempfile.TemporaryDirectory() as d:
        fs, gs = _grp(d, "src.h5")
        _day(gs, UTCDateTime(1997, 5, 5), 7.0)
        fm = h5py.File(os.path.join(d, "master.h5"), "w")
        gm = fm.create_group("XX.TEST")
        merge_channel_v2(gm, "BHZ", gs)
        i = absolute_index(UTCDateTime(1997, 5, 5), 1.0)
        assert gm["BHZ"][i] == 7.0
        assert int((UTCDateTime(1997, 5, 5) - EPOCH) // DAY) in covered_days(gm, "BHZ")
        fs.close(); fm.close()


def test_load_window_masks_never_written_span():
    """The reader must not hand a consumer zeros that look like quiet ground motion.

    Uses an INTERIOR hole (day 1 and day 3 written, day 2 never): a dataset ends at its
    last written sample, so trailing unwritten span is not addressable at all -- it is the
    gaps BETWEEN real days that a consumer could otherwise mistake for quiet recording."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "rover_download"))
    from load_master_h5 import load_window
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.h5")
        f = h5py.File(path, "w"); g = f.create_group("XX.TEST")
        day = UTCDateTime(2012, 6, 1)
        write_channel_day(g, "BHZ", np.full(DAY, 3.0, dtype=np.float32), 1.0, day, "m")
        write_channel_day(g, "BHZ", np.full(DAY, 5.0, dtype=np.float32), 1.0, day + 2 * DAY, "m")
        f.close()
        data, mask, sr, t0, units = load_window(path, "XX", "TEST", "BHZ", day, day + 3 * DAY)
        assert len(data) == 3 * DAY
        assert mask[:DAY].all(), "first written day must be marked real"
        assert not mask[DAY:2 * DAY].any(), "the interior hole must be masked out"
        assert mask[2 * DAY:].all(), "third written day must be marked real"
        assert set(np.unique(data[mask])) == {3.0, 5.0}
