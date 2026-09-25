━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE 2 — DOWNLOAD STRATEGY DECISION & EXECUTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  ROVER's `download` command (the fix confirmed in Stage 1 for the retired-availability
  gotcha) can drive a parallelized, per-station download harness with real, measurable
  throughput — enough to ground a genuine AWS-vs-ROVER cost/time comparison rather than
  a single-station anecdote.

SETUP
  Code: `src/wavenet_pipeline/00_waveform_acquisition/rover_download/pipeline_test.py` (new).
  Run on axon-1 (not mothership — ROVER needs no AWS/EarthScope credentials at all, so
  running here avoided contending with the Stage 1 S3 key-index scan still running on
  mothership at the time). `rover` installed in a scratchpad venv (not yet in the
  project's own `env/`, see Stage 1's env note).
  Stations: first N of the fixed `metadata3/fps_stations.csv` (unchanged), then a
  10-station subset filtered to long-running GSN-type networks (`IU`, `II`, `G`) for a
  cleaner signal. Channel `LHZ`, 1 day (`2018-01-01` -> `2018-01-02`), 10-way parallel.

WHAT WAS TRIED
  1. First attempt (8 arbitrary stations, mixed networks including short-lived temporary
     deployments like `ZU`/`3H`/`XN`): 1/8 succeeded. Most failures were genuinely "no
     data for this network/date/channel" (temporary deployment networks, expected), not
     a pipeline bug.
  2. Second attempt (10 known long-running stations, `IU`/`II`/`G`): **6/10 succeeded
     fully** (download -> preprocess -> package). All 4 failures were network `G`
     (GEOSCOPE) — `G.CRZF`, `G.ROCAM`, `G.AIS`, `G.SSB` all failed with the *identical*
     ROVER ingest error: `"File ... contains data from more than one day"`, despite the
     request window being exactly one calendar day (midnight-to-midnight). All 6
     `IU`/`II` stations succeeded with no such issue. This is a repeatable,
     network-specific pattern (4/4 vs 0/6), not noise — worth a closer look (see Open
     Questions) before assuming it'll recur at the same rate across the full 2,000-station
     set (14 of which are network `G`, `metadata3/fps_stations.csv`'s network
     distribution).
  2b. Root cause not yet fully chased down — candidates: GEOSCOPE's own miniSEED record
      boundary/segmentation conventions, or ROVER's `--timespan-inc`/`--timespan-tol`
      flags (seen in `rover -H download`'s full help, not yet tried) may be the fix.
  3. Isolated a per-worker ROVER repository directory per station (`rover
     init-repository` + its own `rover.config`) specifically to avoid sqlite-contention
     risk from multiple parallel `rover download` processes writing to one shared
     repository — untested whether a shared repo would actually have failed, but the
     isolated-per-station approach matches the existing team pattern in
     `LithoAFR-SWave/parDatDwnld/batch_rover.py` (one SLURM job = one repo, implicitly).

RESULTS (20-task SLURM array, job 31369559, `urseismo`, real completed run, 2026-09-23)
  All 20 tasks entered `RUNNING` immediately (confirms exclusive/no-contention access).
  **6/20 stations succeeded** (`G.SSB`, `G.ROCAM`, `II.HOPE`, `II.NIL`, `IU.PTCN`,
  `JP.JGF`) — the other 14 are all short-lived/temporary-deployment networks (`ZU`,
  `3H`, `XN`, `ZA`, `XA`x2, `S1`, `2A`, `XT`, `3A`, `YY`, `1L`, `BL`, `YT`) genuinely
  lacking `BH`/`LH` data for this window (`download_ok=false`, 0 bytes, no exception —
  a clean "no data," not a crash), matching the anticipated pattern.

  **Linear scaling confirmed** — per-channel-day preprocessing cost across the 5
  "normal" successes (all with `LH` available): 5.34, 5.99, 5.04, 4.72, 6.03 s/channel-day
  — tightly clustered around the single-station baseline (6.05 s/channel-day) despite
  20-way concurrency. **No evidence of provider rate-limiting or shared-filesystem
  degradation at this scale.** The one outlier, `JP.JGF` at 16.11 s/channel-day, is
  explained by that station having no `LH` channel at all (decimating from `BH` only,
  consistent with the earlier BH-vs-LH finding) — a per-station physics difference, not
  a parallelism artifact.

  **BH-only cost, real single-station comparison (`JP.JGF`, same array, same
  conditions — the cleanest apples-to-apples measurement available)**: 26,458,624 bytes
  / 3 channels / 7 days = **~1.26 MB/channel-day raw download** (vs. `LH`'s much smaller
  already-1Hz raw size) and **~16.1 s/channel-day preprocessing** (vs. 5-6s for
  `LH`-available stations) — roughly **2.7-3x more expensive** in both download volume
  and preprocessing time when a station only has `BH`. Final packaged storage is
  identical either way (~324 KB/channel-day, see below) since the output is always
  decimated to 1 Hz regardless of source rate — the BH-vs-LH cost difference is purely
  a download/preprocessing-time cost, not a storage cost.

  **Storage per-channel-day is even tighter and scale-independent**: all 6 successful
  stations land at 323,000-323,800 bytes/channel-day (e.g. `G.SSB`: 13,570,630/6/7 =
  323,110 B; `JP.JGF`: 6,800,401/3/7 = 323,829 B) — confirms the earlier ~323 KB/
  channel-day figure holds regardless of channel type or concurrency, as expected for a
  fixed sample-rate/compression-ratio quantity.

  **These are the confirmed per-channel-day time and storage figures to use for the AWS
  cost comparison** (per PI request, 2026-09-23): ~5-6 s/channel-day preprocessing
  (LH-available stations), ~323 KB/channel-day storage, essentially flat from 1-way to
  20-way concurrency.

Earlier 10-station run, 10-way parallel, 1 day/station, `LHZ` channel (superseded by
the 20-task array above, kept for history):
  - Download: 6/10 succeeded, 3,191,808 bytes (3.04 MB) total, 1.5s wall-clock ->
    **~2.0 MB/s effective aggregate throughput** at this parallelism level.
  - Per-file download time: ~0.8-1.2s regardless of success/failure (the failures still
    downloaded real bytes before failing at ROVER's ingest step — e.g. `G.SSB` pulled
    401,408 bytes in 1.0s before rejection).
  - See `stage_3_preprocessing.md` and `stage_4_packaging.md` for the rest of this same
    test's results.
  - Output artifact: `src/wavenet_pipeline/00_waveform_acquisition/rover_download/pipeline_test_output.h5`
    (not committed — regenerate via `pipeline_test.py`).

HARDWARE TIER LOG
  | Tier            | Status      | Date       | Job ID | Log link |
  |------------------|-------------|------------|--------|----------|
  | axon-1 (local)    | in progress | 2026-09-23 |  n/a   | this session — `pipeline_test.py`, two runs (8-station mixed, 10-station GSN-filtered) — ROVER-based download half now superseded, preprocess/package halves kept as reference |
  | mothership         | not needed  |            |        | ObsPy `MassDownloader` needs no AWS/EarthScope credentials at all |
  | terravibranium      | deprioritized |          |        | has existing ROVER-based `Prj_terraSeis` infra, but ROVER is retired (PI decision, 2026-09-23) — not the target tier going forward |
  | Bluehive             | **prioritized, 20-task array running** | 2026-09-23 | `31369559` (SLURM array 0-19, `urseismo`) | `urseismo` partition confirmed: **5 nodes, 120 CPUs total (117 idle), 189GB+/node, 15-day walltime, exclusive to the group** (`sinfo -p urseismo`) — genuinely massively-parallel, no contention (all 20 tasks entered `RUNNING` immediately across >=3 nodes, zero queue wait). This array tests whether the confirmed single-station numbers (see below) hold linearly at 20-way concurrency or degrade (shared-filesystem contention, provider-side rate-limiting under simultaneous `MassDownloader` queries) — per-station script unchanged from the verified single-station run (1-week window, `BH?,LH?` only, `LH?` preferred, raw SEED kept). |

**Channel-selection decision (PI, 2026-09-23)**: restrict `channel` to `BH?,LH?` only
(excludes `HN` accelerometer/strong-motion channels entirely — not useful for
ambient-noise cross-correlation, and a real, measured cost: G.SSB's first 1-day test
pulled 9 channels including 3 unwanted `HN?`). Within `BH?,LH?`, set
`channel_priorities=["LH?", "BH?"]` — **prefer `LH` over `BH` whenever both exist**,
since `LH` is already 1 Hz (our target decimated rate) while `BH` is typically 20-100 Hz
and would just be decimated away after a much larger download and a much more expensive
`remove_response` FFT deconvolution. Use the wildcard `?` form, **not** `[ZNE]`/`[ZNE]`
component-letter patterns — some stations use numbered orientation codes (`BH1`/`BH2`/
`BH3`) instead of `Z`/`N`/`E`, and a `[ZNE]`-only pattern silently drops those stations
(caught before it caused a real gap, by direct review of the pattern, not by observing a
failure). Both `MassDownloader`'s `channel` restriction and its `channel_priorities`
need this wildcard form — fixing only one still leaves the other silently narrow.
**CONFIRMED (2026-09-23), real 1-week re-test, same station (G.SSB)**: channel
restriction worked correctly — 6 channels selected (not 9), zero `HN`. Both `BH` and
`LH` still appear (3 each) because they exist at *different location codes*, not the
same slot — `channel_priorities` correctly resolves ties only within a shared location,
which is proper behavior, not a bug. Preprocessing dropped from ~34.1s/channel-day (1-day,
9-channel baseline) to ~6.05s/channel-day (7-day, 6-channel run: 254.4s / 6 channels / 7
days) — a real, confirmed **~5.6x per-channel-day speedup**, not just a projection.

**Partition priority order (PI, 2026-09-23)**, for scaling the SLURM array beyond
`urseismo`'s 120 cores if needed: **1. `urseismo`** (5 nodes/120 CPUs, exclusive,
no contention — always fill this first) -> **2. `preempt`** (47 nodes, opportunistic/
lower-priority but large capacity — fine for a restartable, checkpoint-friendly workload
like per-station downloads, where a preempted job just needs re-queueing) ->
**3. `standard`** (91 nodes, general shared queue, real queue-wait risk) ->
**4. `debug`** (12 nodes, short-job only, useful for quick connectivity/smoke tests, not
production volume) -> **5. `gpu`**-family (21+ nodes, not relevant here — no GPU work in
download/preprocess/package, only listed for completeness). Don't reach for tier N+1
until tier N is actually saturated.

DECISION
  **UPDATED (PI, 2026-09-23): ROVER retired as a candidate for this pipeline** — see
  `stage_1_data_availability_cost_analysis.md`'s Decision field. ObsPy `MassDownloader`
  is the chosen tool; **Bluehive is the prioritized execution environment**, extending
  the existing, working `PrjXX_SAmericaNoise/3_Src/4_download_mdl_slurm/
  download_missing_data_slurm_script.py` for our fixed 2,000-station set, rather than
  this session's ROVER-based `pipeline_test.py` or terravibranium's ROVER-based
  `Prj_terraSeis` infrastructure. The GEOSCOPE day-boundary ROVER ingest issue
  documented above is now moot (ROVER retired) — kept here only as a historical record
  of what was tried. `pipeline_test.py`'s download half is superseded; its
  preprocess/package halves (Stage 3/4) remain a useful reference.

OPEN QUESTIONS FOR PI
  - Is the GEOSCOPE (`G` network) day-boundary failure worth root-causing now, or
    acceptable to just retry/pad the request window as a workaround?
  - Confirm scope for the real Stage 2 benchmark: same 10-way parallelism, or scale
    worker count up significantly on terravibranium (48 cores available)?
  - `batch_rover.py` (separate repo, `LithoAFR-SWave`) still needs the `retrieve` ->
    `download` fix identified in Stage 1 before it's usable for the Bluehive tier.

MAJOR UPDATE (2026-09-23, same session) — extensive existing lab ROVER infrastructure found
  Far more prior art exists than initially known. Discovered on `terravibranium`
  (`/RAID6/Prj_terraSeis/`, owned by lab member `bliu`) and `bluehive`
  (`/scratch/tolugboj_lab/`): multiple independent ROVER-based download pipelines —
  `Lucia_WS/Rover/` and `Lucia_WS/GraphNoise/`, `PrjXX_SAmericaNoise/` (the PI's own
  project — canonical original of the `batch_rover.py` toolkit that `LithoAFR-SWave`
  copied), `bliu/Rover_download/`, `Prj10_DeepLrningEq/`. `Prj_terraSeis` alone has ~50
  daily log files (Jul-Oct 2025) from real, large-scale, multi-year production downloads
  (e.g. one station's log shows day 2897/8299 of continuous retrieval in progress).

  **The Oct 2025 logs prove `rover retrieve` genuinely worked then** — real progress
  through thousands of days, no availability-service error. This **dates the
  `fdsnws-availability` retirement to sometime between Oct 2025 and now (2026-09-23)** —
  it did not always fail; this is a real service-side change that broke previously-working,
  actively-used lab infrastructure, not a pre-existing gap.

  **Confirmed on Bluehive too, not just axon-1**: using the PI's own canonical
  `batch_rover.py` (`/scratch/tolugboj_lab/PrjXX_SAmericaNoise/4_Bin/obspy/batch_rover.py`)
  invocation pattern (`rover retrieve {net}_{sta}_*_LH? {start} {end}`) in a scratch test
  venv (`ROVER version 1.0.7`, different version than axon-1's), got the **identical**
  `404`/"This service has been retired" error at `service.iris.edu/fdsnws/availability`.
  The `download`-command fix was also confirmed working on Bluehive (`IU ANMO`,
  195,584 bytes, real data). **This means every existing lab ROVER pipeline listed above
  needs the same one-line `retrieve` -> `download` fix before it works today** — a
  lab-wide issue, not specific to our project.

  Bluehive-specific environment note: `rover`/`mseedindex` are not on the default
  `$PATH` or any base module — needs `pip install rover` into a venv plus
  `module load mseedindex` (module `mseedindex/3.0.5` exists). A cosmetic (non-blocking)
  warning appears both with `retrieve` and `download`: IANA moved its leap-seconds.list
  off the URL ROVER's bundled copy references (`ietf.org` -> `data.iana.org`) — matches
  a `wget` workaround already present in `Prj_terraSeis`'s own scripts.

  Given this scale of existing infrastructure, **the real Stage 2 implementation should
  extend/patch this existing tooling (starting from `PrjXX_SAmericaNoise`'s
  `batch_rover.py`), not replace it with a new script** — `pipeline_test.py` in this
  repo remains useful as a fast, disposable prototype/benchmark harness, not the
  intended production path.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
