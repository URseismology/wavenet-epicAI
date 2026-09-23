━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE 3 — PREPROCESSING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  A simplified version of ADAMA's `GVibToNCFs/pre_pross.py` (detrend, anti-alias
  lowpass, long-period highpass, decimate — no instrument-response removal yet) can run
  fast enough per station-day to not be the bottleneck relative to download.

SETUP
  Code: `src/wavenet_pipeline/00_waveform_acquisition/rover_download/pipeline_test.py`'s
  `preprocess_one()`, run serially (not yet parallelized) immediately after each
  station's download in the same Stage 2 test run.
  Explicit simplification vs. the real `pre_pross.py`: **no instrument-response
  removal** (would require a per-station StationXML fetch — skipped here to keep this a
  throughput/architecture test, not a science-correctness one). Full response removal
  is still required before this counts as scientifically usable output.

WHAT WAS TRIED
  ObsPy pipeline per station-day: `merge(fill_value=0)` -> `detrend('linear')` ->
  `detrend('demean')` -> zero-phase lowpass (0.4 Hz) -> zero-phase highpass (1/3600 Hz)
  -> decimate to 1 Hz -> `detrend('demean')` -> taper (5%). Same filter corners as
  `pre_pross.py`.

RESULTS
  6/6 successfully-downloaded station-days processed without error (same run as Stage 2).
  Timing was **not uniform**: the first station processed (`II.HOPE`) took 1.69s, while
  the remaining 5 took 0.03-0.04s each. This 40x gap is almost certainly first-call
  warmup (ObsPy/SciPy filter-coefficient computation, module import/JIT effects) rather
  than a per-station cost difference — not yet confirmed which specific step causes it.
  **Do not use the 1.69s figure as a per-station estimate**; the steady-state ~0.03s/
  station figure is far more representative, but only n=5 samples deep.

HARDWARE TIER LOG
  | Tier            | Status      | Date       | Job ID | Log link |
  |------------------|-------------|------------|--------|----------|
  | axon-1 (local)    | in progress | 2026-09-23 |  n/a   | see stage_2_download_execution.md — same test run |
  | mothership         | not started |            |        | |
  | terravibranium      | not started |            |        | planned — larger sample needed to separate warmup from steady-state cost, and to test with real instrument-response removal added back in |
  | Bluehive             | not started |            |        | |

DECISION
  Not yet made. Simplified preprocessing is fast enough (~0.03s/station-day steady
  state) that it is very unlikely to be the bottleneck relative to download (~1s/
  station-day) or the full pipeline including response removal (not yet tested).

OPEN QUESTIONS FOR PI
  - Confirm the filter parameters (0.4 Hz lowpass, 1/3600 Hz highpass, decimate to 1 Hz)
    are still the right choice for this project's period band (recall the ML pipeline's
    FTAN grid targets 1-20s periods — worth checking these preprocessing filters don't
    clip anything needed there, even though this is a separate, upstream pipeline).

UPDATE (2026-09-23) — real Bluehive test with instrument-response removal, resolves the
above open question
  Instrument-response removal is no longer an open gap: ObsPy `MassDownloader` fetches
  StationXML automatically (Stage 1/2 finding), so `mdl_bluehive_quicktest.py` now does
  real `tr.remove_response(inventory=inv, output="DISP", water_level=60, pre_filt=...)`
  per channel, no bulk-fetch step needed — it comes for free with each station's
  download. One caveat surfaced, not yet fully resolved: a `UserWarning` on this
  station's StationXML ("file has version 1.2, ObsPy can read versions (1.0, 1.1).
  Proceed with caution" — this lab's `instaseis` conda env has an older ObsPy, 1.2.2).
  Output displacement values were physically sensible (micron-scale for broadband,
  sub-micron for the excluded HN channels) with zero NaNs across all 9 channels —
  reasonable evidence the parse succeeded despite the version mismatch, but not a
  substitute for a real correctness check against a trusted reference (see wavenet-junior
  handoff note below).

  Real G.SSB 1-day timing (9 channels, before the BH/LH-only + LH-preferred fix in
  stage_2): preprocessing took **307.1s total (~34.1s/channel)** — this dominates
  end-to-end wall-clock far more than download (67.8s) or packaging (0.1s). Very likely
  driven by `remove_response`'s FFT deconvolution cost scaling with sample count, which
  is exactly why stage_2's LH-preferred-over-BH decision should cut this cost
  substantially (LH is already at the 1Hz target rate, far fewer samples to deconvolve
  than a 20-100Hz BH trace) — a corrected re-test is in progress to confirm.

  **Handoff for correctness verification**: per PI direction (2026-09-23), wavenet-junior
  should take this script + its documented findings and test against whatever reference
  benchmark they already have, independent of this session's own reasoning above.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
