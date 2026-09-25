import numpy as np
import pyarrow.dataset as ds


def load_day_sets(key_index_dir, station_keys):
    """network.station -> set of year*1000+yearday ids, for the given keys."""
    dataset = ds.dataset(key_index_dir, format="parquet", partitioning="hive")
    tbl = dataset.to_table(columns=["network", "station", "yearday", "year"])
    df = tbl.to_pandas()
    df["key"] = df["network"].astype(str) + "." + df["station"].astype(str)
    df = df[df["key"].isin(station_keys)]
    df["day_id"] = df["year"].astype(np.int32) * 1000 + df["yearday"].astype(np.int32)

    day_sets = {}
    for key, grp in df.groupby("key", observed=True)["day_id"]:
        day_sets[key] = set(grp.tolist())
    return day_sets
