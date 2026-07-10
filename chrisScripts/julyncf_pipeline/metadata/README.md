# metadata

Station inventory, global pair catalogue, and S3 key index used by the cross-correlation pipeline.

---

## Pipeline Flow

The metadata pipeline runs in two environments before any seismic processing begins. `build_key_index.py` (GeoLab) and the Colab notebooks are independent; `keys_partitioned_year/` is uploaded to Colab so `gridmeta.ipynb` can compute temporal overlap without live S3 calls.

---

## Files

| File | Role | Environment |
|------|------|-------------|
| `s3_inventory.ipynb` | Scans S3 + FDSN to produce `s3_inventory.csv` | Google Colab |
| `gridmeta.ipynb` | Pairs stations spatially + temporally → `global_station_pairs_R*.csv` | Google Colab |
| `build_key_index.py` | Scans S3 and writes `keys_partitioned_year/` | GeoLab |
| `XD.RUNG_XD.MTAN.csv` | Validated ADAMA Figure 3 pair for single-pair testing | reference |

The following outputs are gitignored (too large for GitHub) — shown here as examples of what each script produces locally:

| Example output | Source | Size |
|----------------|--------|------|
| `s3_inventory.csv` | `s3_inventory.ipynb` | ~1.5 MB |
| `global_station_pairs_R3_4055.csv` | `gridmeta.ipynb` (H3 res 3) | ~55 MB |
| `global_station_pairs_R4_9407.csv` | `gridmeta.ipynb` (H3 res 4) | ~278 MB |
| `global_station_pairs_R5_15088.csv` | `gridmeta.ipynb` (H3 res 5) | ~679 MB |
| `keys_partitioned_year/` | `build_key_index.py` | ~2.8 GB |

---

## `s3_inventory.ipynb` (Google Colab)

Produces `s3_inventory.csv` — the station inventory used by `gridmeta.ipynb`.

**What it does:**

1. Authenticates with EarthScope via `EarthScopeClient` and lists all networks under `s3://earthscope-mseed.../miniseed/`.
2. Queries FDSN for network descriptions and drops synthetic/test networks (always drops `SY`; whitelists real deployments like `RS`, `XA`, `YV`).
3. Fetches station coordinates and active date ranges from FDSN in 50-network batches.
4. Scans S3 in parallel (20 threads) to collect `(network, station, year, yearday)` tuples.
5. Merges S3 records with FDSN coords, drops records outside the station's active window, and aggregates to unique `(network, station, lat, lon, days)`.
6. Downloads `s3_inventory.csv` from the Colab session.

**Output schema:**

| Column | Description |
|--------|-------------|
| `network` | FDSN network code |
| `station` | Station code |
| `lat` | Station latitude |
| `lon` | Station longitude |
| `days` | Number of days with MiniSEED data in S3 |

---

## `gridmeta.ipynb` (Google Colab)

Produces `global_station_pairs_R*.csv`.

**What it does:**

1. Loads `s3_inventory.csv` and drops stations with fewer than 15 active days.
2. Snaps each station to an H3 equal-area hexagonal grid (Resolution 3, 4, or 5) and keeps only the highest-uptime station per grid cell — this downsamples ~50 k stations to ~4–15 k representatives depending on resolution.
3. Builds a BallTree spatial index and finds all pairs within 60–6000 km.
4. Loads `keys_partitioned_year/` (zip uploaded to Colab) and computes the temporal overlap in days for each spatial pair.
5. Retains pairs with ≥ 15 overlapping days and writes `global_station_pairs_R{N}_{k}.csv`.

**Resolution vs scale (from notebook output):**

| Output file | H3 res | Grid cells | Pairs after overlap filter |
|-------------|--------|-----------|---------------------------|
| `R3_4055.csv` | 3 | ~4 k | ~768 k |
| `R4_9407.csv` | 4 | ~9 k | ~3.9 M |
| `R5_15088.csv` | 5 | ~15 k | ~9.4 M |

Higher resolution → more pairs, more compute at the pipeline stage.

---

## `build_key_index.py` (GeoLab)

Builds `keys_partitioned_year/` — the Parquet S3 key index consumed by `download_pairs.py` at download time.

Run on **GeoLab** (requires EarthScope authentication):

```bash
python build_key_index.py \
    --outdir keys_partitioned_year \
    --workers 20
```

Scans every network in the EarthScope S3 bucket using 20 parallel threads. A full scan takes a few minutes.

---

## Parquet Schema

```
keys_partitioned_year/
  year=2019/
    *.parquet         columns: network, station, year, yearday, dataacess_key
  year=2020/
  ...
```

| Column | Type | Description |
|--------|------|-------------|
| `network` | category | FDSN network code (e.g. `XD`) |
| `station` | category | Station code (e.g. `RUNG`) |
| `year` | int16 | Calendar year (partition key) |
| `yearday` | int16 | Day of year (1–366) |
| `dataacess_key` | str | Full S3 object key for the MiniSEED file |


---

## How `download_pairs.py` Uses It

`build_availability_from_parquet()` filters the index for only the networks and stations needed for a given pair, then builds a `{station: {date: s3_key}}` lookup dict. Overlapping dates between the two stations are then downloaded directly without any S3 listing calls.

---

## `XD.RUNG_XD.MTAN.csv` Schema

```
net1, sta1, lat1, lon1, days1, net2, sta2, lat2, lon2, days2, distance_km
```

The single validated pair for ADAMA Figure 3 reproduction (`XD.RUNG — XD.MTAN`, ~110 km separation, Tanzania).
