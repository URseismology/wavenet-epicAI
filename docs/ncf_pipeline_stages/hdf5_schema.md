# NCF Waveform HDF5 Schema (master file)

Single shared file holding preprocessed, pre-correlation waveforms for every station in
the network. Growable: new stations and new days for existing stations/channels are
added by appending, not by rewriting the file. Not the same schema as the ML pipeline's
FTAN/dispersion HDF5 (`src/wavenet_pipeline/03_machine_learning/`) — this is upstream,
raw-waveform-level data, before any cross-correlation or FTAN step.

## Layout

```
master.h5
  /{network}.{station}/                    <- one group per station, e.g. "G.SSB"
      attrs: network (str), station (str), latitude (float), longitude (float)
      _stationxml_raw                       <- the COMPLETE raw StationXML for this
                                                station, stored once (not per channel,
                                                not re-copied on later appends), as
                                                opaque bytes (np.void). ~0.1 MB/station.
                                                Deliberately the whole file, not
                                                cherry-picked fields (PI, 2026-09-23) --
                                                covers any future metadata need
                                                (orientation, response stages, sensor
                                                type, epochs, ...) without re-fetching,
                                                which is what makes discarding the raw
                                                scratch_work/ directory safe later.
      {channel}                             <- one resizable dataset per channel, e.g. "LHZ"
          dtype: float32
          shape: (n_samples,)  -- grows over time, maxshape=(None,)
          chunks: (86400,)     -- one day at 1 Hz, so appends align to chunk boundaries
          attrs:
            sampling_rate: float (Hz, e.g. 1.0 -- always the post-decimation rate)
            start_time: str, ISO8601 UTC of sample[0] (e.g. "2018-01-01T00:00:01.800000Z")
            units: str, e.g. "m" (displacement) or "counts" if response removal was
                   skipped for that channel (see WHAT'S NOT YET DONE below)
            azimuth, dip: float, degrees -- convenience copy of this channel's
                   orientation at the epoch active when this dataset's data was
                   fetched (also derivable from `_stationxml_raw` above, but kept here
                   too for quick access without re-parsing XML). Needed to rotate
                   numbered-orientation channels (`BH1`/`BH2`, not aligned to true N/E)
                   into standard N/E components before cross-correlation. **Caveat**:
                   if a station's sensor was reoriented mid-history and this dataset
                   spans that change (via later appends), a single azimuth/dip attr no
                   longer describes the whole dataset -- not handled in this v1 schema;
                   check `_stationxml_raw`'s per-epoch channel elements directly for
                   any long-lived station before trusting these two attrs blindly.
```

A station's data is **assumed continuous** within a dataset: sample `i` corresponds to
`start_time + i / sampling_rate`. Gaps are filled with `0.0` at preprocessing time
(`Stream.merge(fill_value=0)`), not left as true gaps — this is what makes simple
index-based time lookup valid without a separate gap table. If a real deployment gap
needs to be distinguishable from a genuine zero-amplitude sample later, that's an open
gap in this v1 schema (see WHAT'S NOT YET DONE).

## Growing the file

**Model: parallel production, serial merge** (confirmed decision, 2026-09-23) — many
workers (e.g. a SLURM array) each independently produce one small HDF5 file per station
(same per-channel layout as above, just not yet merged), then a single coordinator
process (`build_master_h5.py`) appends each of those into the one shared master file.
This is **not** simultaneous multi-writer HDF5 (that needs MPI-parallel HDF5, a real
extra build dependency) and **not** SWMR (single continuous writer + concurrent
readers) — plain h5py, one process writes to the master file at a time, by design.

Two cases when merging a station+channel into the master:
1. **New station or new channel**: create the dataset fresh (`maxshape=(None,)`,
   chunked), write the data, set attrs.
2. **Existing station+channel, new time range**: compute the gap (in samples, via
   `sampling_rate`) between the existing dataset's last sample and the new data's
   `start_time`. If the new data starts after the existing end, resize the dataset to
   fit (`old_length + gap_samples + new_length`), zero-fill the gap if any, then write
   the new samples at the end. If the new data would **overlap** existing samples, the
   merge is refused for that channel (logged, not silently overwritten) — this v1 does
   not attempt to reconcile overlapping/conflicting data.

## What's verified so far (real data, not hypothetical)

- Real per-channel-day storage: **~323 KB/channel-day** (consistent across a 1-day/9-channel
  test and a 1-week/6-channel test on the same station, G.SSB).
- gzip level 4 only saves **~7%** on this data (high-entropy after filtering) — if
  storage matters more than it does today, the lever is `int16` quantization or a
  coarser rate, not compression level.
- Real preprocessing cost: ~6.05s/channel-day when the channel is already at the target
  1 Hz rate (`LH?`), vs ~34.1s/channel-day when decimating from a much higher native
  rate (`BH?`/`HN?`) — this is why channel selection prefers `LH?` over `BH?` (see
  `stage_2_download_execution.md`).

## Real-archive stress test (2026-09-23) — findings that a toy test couldn't surface

Ran the same preprocess->package pipeline against `II.EFI`, a real 27-year (1996-2023,
1,875 real day-directories, 38 GB raw) archive already sitting on Bluehive
(`PrjXX_SAmericaNoise/2_Data/2_RoverDB`, zero download cost) — see
`src/wavenet_pipeline/00_waveform_acquisition/rover_download/preprocess_existing_archive.py`.
Deliberately processes **per-day** (detrend/response-removal/filter/decimate each day
independently, then concatenates already-decimated arrays) rather than "read
everything, merge, process once" — keeps memory bounded regardless of history length.

Real findings, not hypothetical:
- **Non-standard channel orientations are real, not just a theoretical concern**:
  `BH1` at azimuth 343°, `BH2` at 73° (nowhere near standard N/E) — confirms the
  azimuth/dip attrs (added to the schema above) are genuinely needed, not
  over-engineering.
- **Sampling rate is not exactly the nominal value**: `sampling_rate =
  0.999999713897705`, not `1.0` — real instrument clock imprecision. **This schema's
  index math (`sample_i` <-> `start_time + i / sampling_rate`) assumes rate precision
  real data doesn't quite have** — over this station's 27-year span, that tiny relative
  error compounds to an estimated **~243 seconds of accumulated time drift** by the end
  if left uncorrected. Not yet resolved — flagged here as a real, non-obvious
  correctness gap for anyone doing precise multi-year time alignment (e.g. for
  cross-correlation), not just a rounding curiosity.
- **A same-day, same-channel-id, different-sampling-rate conflict** was hit and fixed
  (an instrument/epoch change mid-day in the real archive) — `Stream.merge()` refuses
  this by default; fixed by grouping per (id, rate) and keeping the group with the most
  samples for that day, a deliberate "dominant epoch wins" choice, not a silent drop.
- Per-channel-day cost varies station-to-station more than the earlier small sample
  suggested: this station measured ~9.1 s/channel-day (BH-only, no `LH`) vs. ~16 s/
  channel-day for a different BH-only station tested earlier — real variance, not a bug.

## Instrument-response correctness — RESOLVED (2026-09-23), real trusted-reference check

The single biggest open verification gap (below) is now closed, not just narrowed.
Found a genuine trusted reference: ADAMA's own already-processed output for `GT.BOSA`
(`pre_pross.py`'s SAC output, `/RAID6/bluehiveBackup/Prj5_HarnomicRFTraces/2_Data/
preprocessed_data/DataGT/BOSA/` on terravibranium), independently produced from the
same raw archive we tested against (`GT-BOSA`, backed up on `atos`,
10.17.7.230 — new machine, added to `~/.ssh/config` as `atos` this session).

Ran our own `preprocess_existing_archive.py` against that same raw data (day 1993-058)
and compared numerically against ADAMA's reference SAC for the identical station/
channel/day: **min/max agree to 4-5 significant figures** (ours: -1.6135147e-05 /
1.6283255e-05; ADAMA's: -1.6136357e-05 / 1.6290567e-05), **amplitude std ratio 0.9998**,
**correlation coefficient 0.9523**. Two independently-implemented pipelines applied to
the same raw instrument response and produced near-identical displacement output — this
is real, quantitative correctness evidence, not a plausibility check.

## What's NOT yet done (flag these explicitly to wavenet_junior, don't assume solved)

- No gap-vs-true-zero distinction (see above).
- No cross-correlation step exists yet at all (Stage 5, not started) — this schema is
  the *input* to that, not tested against it yet.
- Overlap-refusal behavior in the merge script is implemented but not stress-tested
  against a real overlapping-rerun scenario.
