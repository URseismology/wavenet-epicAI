# EarthScope Full Key Index — 60,125 Stations, 1.09 PB Archive

**This took ~3 hours to build (2026-09-23, `mothership`, 10.17.7.237) — a full
S3 `ListObjectsV2` scan of the entire EarthScope miniSEED archive.** Treat it as a
valuable, expensive-to-regenerate asset: query it, don't rebuild it, unless the
underlying archive has meaningfully changed.

## What this is

A partitioned Parquet index (partitioned by `year`) mapping every
`(network, station, year, yearday)` in the whole EarthScope archive to its S3 key and
object size — built by
`src/wavenet_pipeline/00_waveform_acquisition/metadata3/build_key_index.py`.

| Metric | Value |
|---|---|
| Networks scanned | 513 |
| Unique stations | 60,125 |
| Total records | 35,026,531 |
| Total raw archive size | **1,091,043.9 GB (~1.09 PB)** |
| Year range | 1969–2046 (2046 is almost certainly a source-archive metadata anomaly, not real data) |
| Build time | 178m18s (~3 hours) |
| Index size (this directory) | 1.8 GB, 62 Parquet files |
| Build cost | ~$0.50 (S3 LIST requests only — `$0.005`/1,000 requests, no data egress at all) |

## Schema

`network` (str), `station` (str), `yearday` (int16), `dataacess_key` (str, the S3 key),
`size_bytes` (int64), `year` (int32, the Hive partition column).

## How to query it (don't scan the whole thing naively — filter first)

```python
import pyarrow.dataset as ds
dataset = ds.dataset("keys_partitioned_year_full", format="parquet", partitioning="hive")
# ALWAYS push a filter down (e.g. by network) before pulling to pandas -- the full
# table is 35M rows. See build_key_index_summary.py for the real pattern used to query
# this down to our fixed 2,000-station network in ~76 seconds on a single laptop.
tbl = dataset.to_table(filter=ds.field("network").isin(["II", "IU", "G"]))
```

## Known data-quality caveat

Network `UW` hit a token-expiry error on its first scan attempt; the retry produced a
count (3,277,000 keys) but this was **not independently re-verified as complete** —
treat `UW`'s numbers with slightly less confidence than the other 512 networks.

## Full derived analysis

See `../DATA_AVAILABILITY_AND_COST_REPORT.md` (queries this index down to our fixed
2,000-station network) and `../key_index_summary/` (the resulting datasets).
