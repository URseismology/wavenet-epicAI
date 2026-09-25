# Handoff for Independent Verification (wavenet_junior)

**Purpose of this doc**: everything needed to independently reproduce, read, and verify
this pipeline's output, without having watched it get built. Read this first; the
per-stage docs (`stage_0` through `stage_4`) are the detailed experiment logs behind
these summaries if you want the full history/reasoning.

**Scope of what you're verifying**: Stage 2 (download) -> Stage 3 (preprocess) ->
Stage 4 (package) of the NCF waveform-acquisition pipeline — real EarthScope waveform
data for ambient-noise cross-correlation, **not** the CPS-synthetic ML pipeline
(`src/wavenet_pipeline/03_machine_learning/`), which is a separate, unrelated
workstream. Test against whatever reference benchmark you already have — this session's
own reasoning (below) should not be taken on faith.

## 1. What the pipeline does, end to end

```
ObsPy MassDownloader  -->  ObsPy preprocess          -->  numpy-direct HDF5   -->  build_master_h5.py
(download + StationXML)    (detrend/response/filter/       (one file per          (merge into one
                             decimate)                       station)               growable master file)
```

Code: `src/wavenet_pipeline/00_waveform_acquisition/rover_download/`
- `mdl_bluehive_quicktest.py` — does download+preprocess+package for **one station**
  (takes a row index into a stations CSV as `sys.argv[1]`). This is the actual,
  as-run, verified script — read it directly, it's short and the comments explain each
  design decision inline.
- `mdl_bluehive_quicktest.slurm` — SLURM array wrapper (`--array=0-19` in the version
  that was run; change the range to cover more stations).
- `bluehive_test_20stations.csv` — the 20-station sample used for verification (first
  20 rows of the real fixed 2,000-station network, `metadata3/fps_stations.csv` —
  **not** a separate/different list).
- `build_master_h5.py` — merges per-station `.h5` files into one growable master file.
- `load_master_h5.py` — loads master-file data into numpy arrays (no obspy dependency,
  just h5py+numpy — usable directly by a cross-correlation script).
- `inspect_h5.py` — a real-structure/compression-ratio inspection helper (dumps
  datasets, shapes, attrs, per-dataset compression ratio via `get_storage_size()`).

## 2. How to reproduce (Bluehive, `urseismo` partition)

```bash
ssh bluehive
source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh
conda activate instaseis   # has obspy, h5py, pandas, working SSL -- see gotcha below

# one station, interactively (fast sanity check before committing to the array):
python3 mdl_bluehive_quicktest.py 0

# all 20, in parallel via SLURM:
sbatch mdl_bluehive_quicktest.slurm

# merge whatever succeeded into one master file:
python3 build_master_h5.py --inputs-dir packaged_h5 --master master.h5

# load and inspect:
python3 load_master_h5.py master.h5                  # list stations/channels
python3 load_master_h5.py master.h5 G SSB LHZ         # load one channel, print stats
python3 inspect_h5.py                                 # edit its `path` variable first
```

**Environment gotcha, already diagnosed — don't re-debug it**: Bluehive's bare
`module load python3` (3.11) has **no SSL module compiled in** — `pip install`/any
HTTPS call fails. Use the `instaseis` conda env above instead (already has everything
working). A from-scratch venv also failed for an unrelated reason (missing
`libpython3.11.so` at runtime unless `module load python3` stays loaded in the *same*
shell session — each new SSH command is a fresh shell, so `module load` doesn't
persist across separate `ssh bluehive "..."` calls).

## 3. The HDF5 schema

Full spec: `docs/ncf_pipeline_stages/hdf5_schema.md`. Short version: one group per
station (`{network}.{station}`), one resizable dataset per channel (`float32`,
`maxshape=(None,)`), attrs `sampling_rate`/`start_time`/`units`. Growable by appending
new stations or new time ranges for existing station+channel — **not** via simultaneous
multi-writer HDF5 (confirmed decision: parallel *production*, serial *merge* — see the
schema doc for why).

## 4. Real, confirmed numbers (not projections) — use these to sanity-check your own runs

- Preprocessing: **~5-6 s/channel-day** when `LH?` is available (already at the 1 Hz
  target rate), **~16 s/channel-day** when only `BH?` exists (needs decimating from a
  much higher native rate) — confirmed via a real single-station test *and* a real
  20-task SLURM array (linear scaling, no measurable slowdown from 20-way concurrency).
- Storage: **~323 KB/channel-day**, essentially constant regardless of channel type or
  concurrency (final output is always 1 Hz).
- gzip-4 compression only saves **~7%** on this data (high-entropy after filtering) —
  don't expect more from tuning compression level.
- Of the 20-station test sample, **6 succeeded** (`G.SSB`, `G.ROCAM`, `II.HOPE`,
  `II.NIL`, `IU.PTCN`, `JP.JGF`); the other 14 are short-lived/temporary-deployment
  networks genuinely lacking `BH`/`LH` data for the 2018-01-01 to 2018-01-08 test
  window — a clean "no data" result (`download_ok=false`, 0 bytes, no exception), not a
  pipeline bug. Don't be alarmed by a similar failure rate on a different date/station
  sample.

## 5. What is explicitly NOT yet verified — please check these independently

- **Instrument-response removal correctness.** Only checked for "physically plausible
  magnitude, zero NaNs" — never compared against a trusted reference (e.g. a
  known-good SAC/RESP-based reduction of the same raw data). A `UserWarning` appeared
  on `G.SSB`'s StationXML ("file has version 1.2, ObsPy can read versions 1.0, 1.1")
  and was not resolved either way — worth specifically checking whether this
  environment's older ObsPy (1.2.2, in `instaseis`) silently mis-parses anything from
  newer-schema StationXML files.
- **Cross-correlation itself does not exist yet** (Stage 5, not started) — this
  pipeline's output is the *input* to that, untested against it.
- **Overlap handling in `build_master_h5.py`** is implemented (refuses and logs rather
  than silently overwriting) but not stress-tested against a real overlapping re-run.
- **`units` attribute is only populated by new runs** — the already-produced test files
  referenced above predate that fix and will show `units=unknown` when loaded; this is
  expected, not a bug in `load_master_h5.py`.

## 6. Station-pair verification against a real archive + trusted reference (new, 2026-09-23)

A real, independent archive exists on a new machine, **`atos`** (10.17.7.230, alias
added to `~/.ssh/config`, no `scp`/`sftp` — it's a Synology NAS; use a piped
`ssh atos "cat file" > local_file` transfer instead): raw SEED for the `GT` network
(South African/African long-running stations), ROVER-datarepo layout, e.g.
`/volumeUSB2/usbshare/Archive/terravibranium_RAID6/bluehive_bk_monthly/
obspy_batch_bk_Oct062021/GT/GT-BOSA/datarepo/data/GT/{year}/{doy}/`. No download
needed — this is exactly what `preprocess_existing_archive.py` (same directory as the
other scripts) is for.

**A trusted reference exists for this exact data**: ADAMA's own already-processed
output for `GT.BOSA`, `/RAID6/bluehiveBackup/Prj5_HarnomicRFTraces/2_Data/
preprocessed_data/DataGT/BOSA/` on terravibranium (SAC files, e.g.
`BOSA.1993.058.00.00.00.LHZ.sac`). **This session already ran the correctness check**:
processed the same raw day through `preprocess_existing_archive.py` and compared
numerically against that SAC reference — min/max agreed to 4-5 significant figures,
amplitude std ratio 0.9998, correlation 0.9523 (see `hdf5_schema.md`'s "Instrument-
response correctness" section for the full writeup and `compare.py`-style comparison
approach, not yet committed as a repo script but trivial to reproduce: load both with
ObsPy, slice to the same day/sample count, `np.corrcoef`). **Reproduce this yourself
independently** rather than trust this session's numbers alone.

**Station-pair test** (the actual ask: verify packaging+reading works for a pair, ready
for NCF): `GT-BOSA` (South Africa) and `GT-DBIC` (Ivory Coast) are both real, substantial
archives (~100+ GB raw each) at genuine long-distance separation, appropriate for
long-period surface-wave cross-correlation. Suggested steps:
1. Run `preprocess_existing_archive.py` once per station (bounded — use `--limit-days`
   for a first pass, e.g. 30, not the full multi-decade history) against each station's
   `datarepo/data/GT` path on `atos`.
2. `build_master_h5.py --inputs-dir <dir-with-both-.h5-files> --master pair_test.h5` to
   merge both stations into one file.
3. `load_master_h5.py` (or `load_channel_range`) to pull matching, overlapping time
   windows for both stations' vertical (`LHZ`/`BHZ`) channels.
4. Sanity-check readiness for NCF: confirm both series cover the *same* real calendar
   range (a correlation needs simultaneous data, not just any two stations' data), then
   try ObsPy's own `obspy.signal.cross_correlation.correlate` on the two loaded arrays
   as a basic "does this even run and look non-degenerate" smoke test — this pipeline's
   actual Stage 5 (NCF computation) doesn't exist yet, so this is a readiness check, not
   a claim that Stage 5 is done.

## 6b. Recommended follow-up: a paper-validated station pair, no `atos` access needed

Per PI direction (2026-09-23): **`XD.MTAN` and `XD.RUNG`, channel `BHZ`**, are stations
used in a prior lab paper — a scientifically-motivated pair, not an arbitrary one.
**Confirmed available via the public FDSN service** (`service.earthscope.org`, the same
one `MassDownloader` already uses) — checked directly this session:
- `XD.MTAN`: lat -7.9073, lon 33.3203, deployed 1994-05-25 to 1994-10-30
- `XD.RUNG`: lat -6.9372, lon 33.5180, deployed 1994-05-25 to 1995-05-16
- Both ~110 km apart (East Africa, likely a 1994 PASSCAL-style deployment), **overlapping
  data window 1994-05-25 to 1994-10-30** (~5 months) — genuinely valid for
  cross-correlation, not just structurally present.
- Real waveform data confirmed retrievable (`BHZ`, a 10-minute test window returned
  16,384 bytes, HTTP 200).

**This means `atos` access is not needed for this specific test** — use the existing,
already-proven download path (`mdl_bluehive_quicktest.py`'s `MassDownloader` pattern,
or its adapted SLURM array) pointed at `network="XD", station="MTAN"` and
`station="RUNG"`, `channel="BH?"` (this deployment predates modern `LH?`-preference
optimization relevance — both networks only ran at 20 Hz `BH`/1 Hz `LH`, either works,
`BHZ` specifically was asked for), over the confirmed overlapping window. This is a
**download-based** test (unlike the `atos`-archive tests above, which were
download-free) — expect real download/preprocess time and volume per the confirmed
Stage 2/3 numbers in `stage_2_download_execution.md`.

If a *different*, `atos`-only dataset is ever needed instead (not the case for this XD
pair), that would require provisioning `atos` SSH access for the `wavenet-junior`
account specifically (same one-time `ssh-copy-id` pattern used for `urseismoadmin` this
session) — ask the PI to arrange the `atos` account/access itself, since this session's
own access was granted directly by the PI, not something a team member can self-serve.

## 7. Reference: the fixed station network these numbers come from

`src/wavenet_pipeline/00_waveform_acquisition/metadata3/fps_stations.csv` — 2,000 stations,
farthest-point-sampled for even global coverage. **Do not regenerate or modify this
file or `fps_downsample.py`** — station selection is a locked decision (see
`stage_0_station_pair_preanalysis.md`).
