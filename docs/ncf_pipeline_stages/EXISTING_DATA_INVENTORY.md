# Existing Raw/Processed Seismic Data Inventory (atos, terravibranium, Bluehive)

**Purpose**: a living catalog of seismic waveform data already sitting on lab machines
from prior projects — so future work runs our packaging pipeline (preprocess -> HDF5)
against what already exists instead of redownloading it. Compiled 2026-09-23 from
direct `find`/`du -sh` checks during this session's NCF pipeline verification work —
update this file as new locations are found or existing ones get re-checked.

**Status legend**: `CONFIRMED` = size/structure directly checked this session.
`FOUND, NOT INSPECTED` = location located via `find`, contents not yet verified —
treat size/format claims here as unconfirmed until checked.

---

## atos (10.17.7.230, Synology NAS, alias `atos` in `~/.ssh/config`)

No `scp`/`sftp` (Synology quirk) — use `ssh atos "cat file" > local_file` to move data.
Passwordless access currently set up for `urseismoadmin` only; other accounts need their
own arranged (ask PI, not self-serve).

| Path | Content | Size | Status |
|---|---|---|---|
| `/volumeUSB2/usbshare/Archive/terravibranium_RAID6/bluehive_bk_monthly/obspy_batch_bk_Oct062021/` | Raw SEED, ROVER-datarepo layout (`{net}/{net}-{sta}/datarepo/data/{net}/{year}/{doy}/`), 20+ networks: `2H,3D,AF,DW,GE,GT,GY,HL,IU,KO,LX,MN,PM,TT,XA,XB,XG,XJ,XV,XW` (partial list, more likely present) | GT-BOSA 132G, GT-DBIC 116G, GT-LBTB 104G, GT-BGCA 21G (all `CONFIRMED`); II-EFI 38G (`CONFIRMED`, real 1996-2023/1875-day archive, see `hdf5_schema.md`'s real-archive-stress-test section); II-HOPE/JTS/NNA ~272K each (`CONFIRMED` — essentially empty, logs only, no real waveform data despite station dirs existing) | `CONFIRMED` (spot-checked) |
| `/volumeUSB1/usbshare/Archive/repovibranium_bluehive/daily/Prj10_DeepLrningEq/2_Data/obspy_data/` | Single unlabeled station (no net/sta in filenames, just `{date}-{channel}.mseed`), `HH?` channels | 110 MB, 4 days (2018-09-05 to 2018-09-08), 12 files | `CONFIRMED` — too small/narrow alone for a station-pair test |
| `/volumeUSB5/usbshare/Archive/terravibranium_RAID6/bluehiveBackup/Prj10_DeepLrningEq/9_DanielSequencer/` and `.../2_Data/obspy_data/` | Appears to be another copy/mirror of the Prj10_DeepLrningEq data above | unknown | `FOUND, NOT INSPECTED` |
| `/volumeUSB1/usbshare/Archive/repovibranium_bluehive/daily/Prj10_DeepLrningEq/9_DanielSequencer/` | Sibling dirs at this level: `ConvNetQuake`, `GPD`, `NGtest`, `africa_data`, `nigeria_network`, `nigeria_network_2` (names suggest seismic phase-picker DL projects + regional multi-station datasets) | unknown | `FOUND, NOT INSPECTED` — `nigeria_network`/`africa_data` look like the more promising candidates for real multi-station data if `obspy_data` alone isn't enough |
| Other USB shares (`volumeUSB1`-`volumeUSB5` seen to exist) | Not fully enumerated | unknown | `NOT SEARCHED` |

## terravibranium (`ssh tolugboj@terravibranium.earth.rochester.edu`)

| Path | Content | Size | Status |
|---|---|---|---|
| `/RAID6/bluehiveBackup/Prj5_HarnomicRFTraces/2_Data/preprocessed_data/` | **Processed SAC** (ADAMA's `pre_pross.py` output), one `Data{NET}/{STA}/` dir per network: confirmed networks include `1C,2H,3D,6A,7C,8A,AF,BX,DW,ES,G,GE,GT,GY,HL,II,IM,IP,IU,KO,...` (list continues, not fully enumerated) | `DataGT/BOSA` used directly this session as a **trusted reference** — real, quantitative correctness check against our own pipeline's output (correlation 0.9523, amplitude match to 4-5 sig figs, see `hdf5_schema.md`) | `CONFIRMED` for `DataGT/BOSA`; other networks `FOUND, NOT INSPECTED` |
| `/RAID6/bluehiveBackup/Prj12_AfrTxRF/2_Data/SAC/XD/RUNG/` | SAC files for `XD.RUNG`, filenames like `XD.RUNG.NN..BHZ.SAC` (numeric index, not date) — **naming pattern suggests event-cut receiver-function segments, not continuous-day traces** | unknown count/size | `FOUND, NOT INSPECTED` — do not assume this is comparable to our continuous-waveform output without checking actual trace duration first (see the 2026-09-23 memo's Task 2 note to wavenet_junior) |
| `/RAID6/Prj_terraSeis/output/` | **Raw SEED**, ROVER `retrieve`-based (now-superseded download tool), regional (North America-focused) | **2.5 TB total** (2.2 TB in `North_America_data/` alone) | `CONFIRMED` — cited as the real-world "why raw SEED must be discarded" example |
| `/RAID6/Prj_terraSeis/source/`, `/RAID6/Prj_terraSeis/logs/` | Scripts + ~50 daily log files (Jul-Oct 2025) from that project's real production ROVER downloads — proved `rover retrieve` worked as of Oct 2025, dating the FDSN-availability-service retirement to sometime between then and now | n/a | `CONFIRMED` |

## Bluehive (`ssh bluehive`, `/scratch/tolugboj_lab/`)

| Path | Content | Size | Status |
|---|---|---|---|
| `PrjXX_SAmericaNoise/2_Data/2_RoverDB/` | Raw SEED, ROVER-datarepo layout, South-America-focused, real multi-station archive (own `batch_rover.py`/`download_missing_data_slurm_script.py` toolkit — the canonical source `LithoAFR-SWave`'s copy came from) | **11 TB** | `CONFIRMED` |
| `PrjXX_SAmericaNoise/2_Data/2_RoverDB_archive/` | Presumably an older/archived version of the above | unknown | `FOUND, NOT INSPECTED` |
| `Prj10_DeepLrningEq/9_DanielSequencer/datarepo/` | Small ROVER datarepo, no obvious `obspy_data`/`2_Data` subpath matching the atos copy | small (logs + sqlite index only observed) | `CONFIRMED` (partially — did not find actual waveform files here, may be a stub/config-only copy vs. the real data on atos) |
| `Lucia_WS/Rover/`, `Lucia_WS/GraphNoise/`, `bliu/Rover_download/` | Multiple other lab members' own ROVER-based download attempts | unknown | `FOUND, NOT INSPECTED` |

---

## How to use this inventory

Per PI direction (2026-09-23): **don't redownload what's already here.** Once
mothership's AWS-side cost analysis is in and a real acquisition strategy is set, the
next step for any of the `CONFIRMED` raw-SEED locations above is to run
`preprocess_existing_archive.py` (see `HANDOFF_wavenet_junior.md`) directly against
them — zero download cost, same script already proven on `II-EFI` and `GT-BOSA`. The
`CONFIRMED` processed-SAC locations (`Prj5_HarnomicRFTraces`) are valuable as
**trusted references** for correctness checks on overlapping raw data, not as data to
re-process themselves.

Before trusting any `FOUND, NOT INSPECTED` row for real work: check it the same way the
`CONFIRMED` rows were checked here (`du -sh`, a sample `find` for real filenames, and —
for SAC data specifically — read one file's `stats.npts`/duration to confirm it's
continuous-day data, not event-cut segments, before assuming it's comparable to our
output).
