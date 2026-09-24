# Global Ambient-Noise Waveform Acquisition Pipeline — Progress

At-a-glance checklist for the pipeline that gets a global, spatially-even set of station
waveforms from EarthScope into a packaged, preprocessed form ready for cross-correlation
(NCF) computation. Detailed experiment logs are in the per-stage docs linked below.
Separate from `docs/ml_pipeline_stages/` (the FTAN/U-Net dispersion-curve ML pipeline,
which *consumes* CPS-simulated data, not EarthScope waveforms) — the two are independent
workstreams under the same project.

Source code for this pipeline lives at `src/wavenet_pipeline/00_waveform_acquisition/` (currently
on the unmerged `add-september-ncf-pipeline` branch).

## Deployment strategy (PI, 2026-09-23)

Three-phase rollout, not an either/or choice between Bluehive and AWS:

1. **Test and run extensively on Bluehive first.** It's proven, familiar, and
   effectively free to us (already-allocated academic compute, `urseismo` partition).
   Package the pipeline into a **Docker image** once it's solid there — makes the whole
   thing portable, not just a pile of scripts tied to one cluster's environment quirks
   (e.g. the `instaseis` conda env / SSL gotchas documented in this session).
2. **Run and test AWS for a few stations** — the same verify-small-before-scaling
   discipline already applied throughout this pipeline's development, now applied to
   comparing platforms, not just comparing scale on one platform. **Station selection
   for this pilot should maximize continental diversity** — prioritize regions not yet
   exercised this session (Canada, Arctic/Antarctic, Australia, broader Eurasia) over
   re-testing the same US/Africa-heavy stations already used, to genuinely exercise
   different real-world archive conditions, not just re-confirm familiar ground.
3. **Deploy on both AWS and Bluehive**, split by some permissible cost/time model — the
   real numbers in `DATA_AVAILABILITY_AND_COST_REPORT.md` §6 (speed/time/dollar cost at
   various parallelization levels) are exactly the comparison basis for that model,
   directly against the real Bluehive numbers already measured in this same pipeline
   (single-station, 20-task array, 27-year stress test).

**Why AWS at all, given Bluehive is free**: AWS's value isn't just compute — it's
**reach**. It makes the code and any improvements available to the world, not locked
to one university's private HPC systems, and it's realistic to fund that via **cloud
research/dollar grants** (common for academic work) rather than lab budget — meaning
the wider distribution benefit can come at effectively no net cost, while Bluehive
remains the reliable "do the actual science for free" workhorse. Not a contradiction —
use each for what it's good at.

**Existing data inventory** (don't redownload what's already on a lab machine):
[EXISTING_DATA_INVENTORY.md](EXISTING_DATA_INVENTORY.md) — catalog of raw SEED and
processed SAC already sitting on `atos`/`terravibranium`/`Bluehive` from prior projects.

**Independent verification handoff (wavenet_junior)**:
[HANDOFF_wavenet_junior.md](HANDOFF_wavenet_junior.md) — everything needed to
reproduce, read, and verify Stage 2-4's output independently: the code, the HDF5
schema ([hdf5_schema.md](hdf5_schema.md)), real confirmed cost numbers, and an explicit
list of what's NOT yet verified.

| Stage                                         | axon-1 | mothership | terravibranium | Bluehive | Doc |
|------------------------------------------------|:------:|:----------:|:--------------:|:--------:|-----|
| 0. Station & Pair Pre-Analysis (spatial)         |  [x]   |    n/a     |       n/a       |   n/a    | [stage_0_station_pair_preanalysis.md](stage_0_station_pair_preanalysis.md) |
| 1. Data Availability, Size & Cost Analysis         | [x]  |    [x]     |       —         |    —     | [stage_1_data_availability_cost_analysis.md](stage_1_data_availability_cost_analysis.md) |
| 2. Download Strategy Decision & Execution           | [~]  |    [ ]     |      [ ]        |   [ ]    | [stage_2_download_execution.md](stage_2_download_execution.md) |
| 3. Preprocessing (adapt ADAMA `pre_pross.py`)         | [~]  |     —      |      [ ]        |   [ ]    | [stage_3_preprocessing.md](stage_3_preprocessing.md) |
| 4. Packaging (numpy-direct HDF5, no SAC round-trip)     | [~] |     —     |      [ ]        |    —     | [stage_4_packaging.md](stage_4_packaging.md) |
| 5. Cross-Correlation (NCF computation)                    | — |     —     |      [ ]        |   [ ]    | stage_5_cross_correlation.md (not started) |

`[x]` = complete and reviewed, `[~]` = in progress, `[ ]` = not started, `—` = not
applicable for that tier, `n/a` = tier doesn't apply to this stage at all.

## Production framework (built + verified 2026-09-24)

`src/wavenet_pipeline/00_waveform_acquisition/production/` is the real, run-at-scale
implementation of Stages 2-4 for all 2,000 stations -- a master/orchestrator/logger/
inspector split (PI's design, 2026-09-23), built on top of the exact verified
download/preprocess/package logic from `rover_download/mdl_bluehive_quicktest.py`:

- **`orchestrator.py`** -- one SLURM array task per station: download -> preprocess ->
  package to a per-station HDF5 shard. Idempotent (skips a station whose result JSON
  already shows `package_ok`), so a partial/failed range can be resubmitted safely.
- **`logger.py`** -- single long-lived process, the only writer to the shared master
  HDF5 ("parallel production, serial merge" -- reuses `build_master_h5.py`'s
  `merge_channel`). Appends a row per merged station to `master_log.csv`.
- **`inspector.py`** -- single long-lived process, independently re-verifies each merged
  station against the master file (channel presence, length, not all-zero/NaN) and only
  THEN purges that station's raw SEED scratch dir -- the per-station mechanism for this
  doc's "discard only once proven solid" principle.
- **`master.py`** -- `init`/`submit`/`status`/`progress`/`stop` CLI that renders and
  submits the three SLURM scripts.

**Verified via a real 15-station end-to-end run (2026-09-24)**: full cycle
download -> merge -> independent verify -> purge confirmed correct (merged=5,
verified+purged=5, matching exactly).

**Real infra issues found and fixed during verification** (all confirmed by direct
testing, not guessed):
1. `master.py` itself runs inside an activated conda env (needs pandas); `sbatch`
   propagates the submitting shell's environment by default, so every job it submitted
   was double-activating `conda activate instaseis` (once inherited, once inside the
   script) -- this reproducibly broke `pkg_resources`'s vendored `packaging` submodule
   resolution (`ImportError: cannot import name '_manylinux'` at `from obspy import
   ...`), 5 separate trials, regardless of jitter/retries/a concurrency throttle/
   precompiled bytecode. Fixed with `sbatch --export=NONE`.
2. Two live `logger`/`inspector` processes against the same root race on their own
   in-memory state and can double-process a station (found via a leftover job from an
   earlier failed test that was never `scancel`'d). Fixed with a PID lock
   (`lockutil.py`) -- a second instance now refuses to start instead of racing.
3. `inspector.py`'s exit condition originally only checked "the orchestrator finished
   and everything CURRENTLY in the ledger is handled" -- which can be true before the
   logger has actually merged the last successful station, causing an early exit that
   left a fully-successful station unverified/unpurged. Fixed to also require the
   ledger row count to match the actual success count from `results/`.

**This cluster requires `--qos` to match `-p` 1:1 by partition name** (`sbatch -p
standard` alone fails "Invalid qos specification"; `-p standard --qos=standard`
succeeds) -- confirmed directly, and a single sbatch call can't mix partitions with
different implied QOS. So "use every partition, not just `urseismo`" (PI, 2026-09-23) is
implemented as **splitting the array across separate sbatch submissions, one per
partition with its own matching `--qos`** (`master.py submit`'s default:
`urseismo,standard,preempt,debug,interactive` -- confirmed working across all five in
the same verification run, including a `standard`-partition chunk that was transparently
preempted and requeued mid-run, which orchestrator.py's idempotency handled with no
manual intervention).

**Storage**: every path used by this framework lives under `/scratch/tolugboj_lab`,
never `$HOME` -- confirmed via `circ-quota` that `$HOME` is already at 14.4/20 GB soft
limit from pre-existing personal package installs (`~/.local` 5.5GB, `~/.cache` 2.4GB,
neither created by this framework), real headroom to protect. `MPLCONFIGDIR`/
`XDG_CACHE_HOME` are redirected to the run's own `{root}/.cache/` in every SLURM
template as an extra guard against incidental cache writes landing in `$HOME`.

**Progress tracking**: `master.py progress --root ROOT` prints a text progress bar
(orchestrator tasks reported / manifest total), GB downloaded and packaged so far, and
-- once called at least twice -- a rate (stations/hr, GB/hr) and ETA to completion,
computed from a snapshot log it appends to on each call.

**Not yet run at full 2,000-station scale** -- the 15-station run above is the
verification step; scaling up is the next action (`master.py init` with no
`--n-stations` + `submit`), still gated on the same "final band/duration decision"
already flagged as an open PI call earlier in this doc.

**Fixed decision, do not revisit without explicit PI approval**: the 2,000-station set
in `metadata3/fps_stations.csv` (farthest-point-sampled for even global coverage) is
**not to be changed** by any later stage. Only connection *length* (distance band) and
*duration* (shared-recording-days threshold) parameters may be relaxed — see Stage 0/1
docs.

**Acquisition path decision (PI, 2026-09-23, final for now)**: ROVER is retired as a
candidate for this pipeline — it needed a `retrieve`->`download` workaround for the
retired `fdsnws-availability` service and still doesn't fetch instrument-response
metadata. **ObsPy `MassDownloader` is the chosen tool** (works today with no fix
needed, fetches StationXML automatically, structurally unaffected by the
availability-service retirement). **Bluehive is the prioritized execution environment**
going forward, since a working, already-proven `MassDownloader` implementation already
exists there (`/scratch/tolugboj_lab/PrjXX_SAmericaNoise/3_Src/4_download_mdl_slurm/`) —
extend that script for our fixed 2,000-station set rather than building new tooling or
using terravibranium's ROVER-based `Prj_terraSeis` infrastructure. This session's own
`rover_download/pipeline_test.py` prototype is retained only as a reference for the
preprocess/package steps (ObsPy pipeline, numpy-direct HDF5) — its ROVER-based download
half is superseded.

**North Star sizing target (set 2026-09-23, keep this as the reference framing for all
cost/size estimates going forward)**: What is the size of the final packaged file for
**all 2,000 stations**, where daily preprocessed data is only acquired, preprocessed,
and packaged **to the extent needed to ensure every station has at least one valid
connection** (i.e. degree >= 1 in the pair graph) — not "download everything a station
ever recorded." This is a minimum-edge-cover sizing problem over the existing pair graph
(`fps_pairs.csv` or a relaxed-band variant): pick a minimum set of pairs such that every
one of the 2,000 stations touches at least one selected pair, then size = the union of
each selected pair's *overlap* days (not all days), at *preprocessed/packaged* size
(post-decimation, e.g. `pipeline_test.py`'s ~320 KB/station-day at 1 Hz — NOT the raw
S3 `size_bytes` from the Stage 1 key index, which is far larger). Compute this once
Stage 1's full key index and `pair_size_estimate.csv` are ready.

**Design principle (set 2026-09-23, informed by prior lab experience)**: raw miniSEED
must be treated as transient/ephemeral in the eventual **production** pipeline —
download, preprocess to the packaged HDF5 format, then discard (or tar as a short-lived
backup only, not a permanent archive). Keeping full raw archives around long-term was a
real, costly mistake in prior lab projects — real number, not hypothetical:
`/RAID6/Prj_terraSeis/output/` on terravibranium totals **2.5 TB** of raw SEED (2.2 TB
in `North_America_data/` alone), for a regionally-scoped project, not even a global
2,000-station one. Even bigger real number found since:
`/scratch/tolugboj_lab/PrjXX_SAmericaNoise/2_Data/2_RoverDB` on Bluehive is **11 TB**.

**Revised (PI, 2026-09-23): during the current verification phase, do NOT discard raw
SEED yet.** Re-fetching is costly (time, provider load) and keeping the raw input makes
debugging preprocess/package issues possible without a re-download. Discard only once
the pipeline is proven solid end-to-end — this is a deliberate, temporary exception to
the discard principle above, not a reversal of it. `mdl_bluehive_quicktest.py`
(Bluehive) currently keeps raw SEED via a `KEEP_RAW_SEED_FOR_DEBUGGING` flag, default
`True` — flip once verified. `pipeline_test.py` (axon-1, superseded) still
unconditionally discards; not being re-run, so left as-is.

Rule (mirrors `docs/ml_pipeline_stages/PROGRESS.md`): whoever completes a stage/tier
update updates two things in the same commit — the cell here, and that stage's Hardware
Tier Log row in the detailed doc.

Last updated: 2026-09-23 (Stage 1 in progress this session).
