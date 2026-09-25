━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE 4 — PACKAGING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  Preprocessed waveforms can be written directly from in-memory numpy arrays into HDF5
  via `h5py`, skipping the SAC-file materialization step that ADAMA's
  `DataReaderWriter/ppToHDF5.py` currently requires — per the PI's explicit scoping note
  (2026-09-23): this intermediate file should hold *only* preprocessed, pre-correlation
  waveforms, not the fuller FTAN/dispersion schema used downstream by
  `src/wavenet_pipeline/03_machine_learning/` (a separate, unrelated pipeline).

SETUP
  Code: `src/wavenet_pipeline/00_waveform_acquisition/rover_download/pipeline_test.py`'s
  `package_to_hdf5()`. One shared HDF5 file for the whole test batch
  (`pipeline_test_output.h5`, not committed — regenerate via the script), group-per-
  station (`{network}.{station}`), one dataset per channel, `gzip` compression level 4.
  Contrast with the current `ppToHDF5.py`: that script reads SAC files back off disk
  (`obspy.read(path)`) and appends whole `Stream` objects via ObsPy's native `'H5'`
  writer — a real disk round-trip this design avoids entirely.

WHAT WAS TRIED
  Directly wrote each preprocessed trace's `float32` numpy array into an HDF5 dataset
  with `sampling_rate` and `starttime` as attributes — no SAC file ever touched disk.

RESULTS
  6 station-day datasets written in 0.04s wall-clock (negligible next to
  download/preprocess). Output file: 1,931 KB total (gzip-compressed) for 6 station-days
  of 1 Hz LHZ data — roughly **320 KB/station-day** at this sample rate/compression,
  though this is a single, small sample and not yet a reliable per-scale estimate.

HARDWARE TIER LOG
  | Tier            | Status      | Date       | Job ID | Log link |
  |------------------|-------------|------------|--------|----------|
  | axon-1 (local)    | in progress | 2026-09-23 |  n/a   | see stage_2_download_execution.md — same test run |
  | mothership         | not started |            |        | |
  | terravibranium      | not started |            |        | planned — real chunking/compression strategy decision needs a much bigger sample (thousands of station-days, multiple channels) before committing |
  | Bluehive             | not started |            |        | |

DECISION
  Numpy-direct HDF5 writing (no SAC round-trip) is confirmed viable at small scale and
  matches the PI's explicit scoping. Chunking layout, compression level, and whether one
  file vs. many (e.g. per-family, matching the ML pipeline's own HDF5 organization
  convention) is **not yet decided** — needs a larger test.

OPEN QUESTIONS FOR PI
  - One HDF5 file for the whole 2,000-station network, or split (e.g. by family/region,
    mirroring how the ML pipeline already organizes its own HDF5 output)?
  - Should this schema include any provenance metadata (e.g. which download run
    produced it, preprocessing filter parameters used) as file-level attributes, so a
    downstream cross-correlation step can trace back how the data was made?
  - Compression level/chunking tuned for what — smallest file size, or fastest random
    read access during Stage 5 (cross-correlation)?

UPDATE (2026-09-23) — real inspection of a Bluehive-packaged file, gzip compression
finding
  Directly inspected `G.SSB.h5` (1-day test, 9 channels, `h5py.File.visititems` + a
  real per-dataset `get_storage_size()` check, not just "the file exists"): structure is
  sound — 1 group (`G.SSB`), 9 datasets (one per channel), `float32`, ~86,400 samples
  each (1 Hz x 1 day, small per-channel variance from real start-time offsets), `attrs`
  (`sampling_rate`, `starttime`) present and correct, **zero NaNs across all 9
  channels**, values physically sensible for displacement output (micron-scale for
  broadband channels, consistent with correct `remove_response` output).

  **gzip-4 compression only saved ~7%** (e.g. one channel: 345,604B uncompressed ->
  321,698B stored) — preprocessed float32 seismic waveform data is high-entropy
  (noise-like after filtering), so gzip barely helps. **If storage becomes a real
  concern at 2,000-station scale, the lever is `int16` quantization or a coarser sample
  rate, not gzip level** — this is now a data point, not a guess.

  Total on-disk size for this 1-day/9-channel station: 2,922,454 bytes (2.92 MB).

  **CONFIRMED re-test (2026-09-23), same station, 1-week window, corrected channel
  selection**: 6 channels (3 `BH` + 3 `LH`, at different location codes — both
  legitimately kept, see stage_2), 604,770-604,873 samples each (= 7 days x 86,400
  samples/day, confirming the week-long cross-day merge worked correctly), **zero NaNs
  across all 6 channels**, same ~7% gzip compression finding holds. Total: 13,570,630
  bytes (13.57 MB) for 1 station x 1 week x 6 channels — raw SEED was correctly kept
  this run (`KEEP_RAW_SEED_FOR_DEBUGGING=True` fix confirmed working,
  `scratch_work/G_SSB/` still present).

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
