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

**Scope decision made (PI, 2026-09-24), superseding the "gated" note above**: download
all available history for every station, not a connectivity-gated subset -- "the goal
is to get all the data." Real numbers from the key index: **78.9 TB total**, spanning a
few dozen days up to **56 years** for the longest-running stations (avg real year-span
only 5.55 years -- most of that volume/time sits in a long tail of ~40 outlier
stations). This makes the North Star minimum-coverage framing above moot for the
current push (superseded, not deleted -- it's still the right lens if scope ever gets
re-bounded later).

## Full-history rollout, real bugs found and fixed (2026-09-24, after the 15-station verification above)

Launching at real full-history scale surfaced three more real, load-bearing problems
beyond the four infra bugs above -- each found by direct testing, each fixed and
re-verified before moving on, not guessed at:

**1. ObsPy itself breaks on very wide date-range requests.** A full 2,000-station launch
hit 0% success across ~800 completed stations. Cause: `MassDownloader` has an internal
bug querying IRIS's availability endpoint over a many-decade span (`TypeError: sequence
item 0: expected str instance, tuple found`) -- sometimes raised (crashing the station),
sometimes silently swallowed inside ObsPy and reported as "no data" (a **silent
data-loss** failure mode, confirmed directly: re-ran a "no data" station, `IU.MA2`, by
hand and it came back with real 1993-2013 data). Fixed: `orchestrator.py`'s download
loop now requests one **year** at a time instead of one call spanning the full range --
every prior verification test (1-week windows) already proved a narrow span is safe.

**2. The per-station year loop originally spanned the full 1970-2026 range for EVERY
station, regardless of real deployment length.** Found while investigating why real
observed download throughput (~23 KB/s per connection, from real canary telemetry) was
~43x slower than the ~1 MB/s originally assumed: a station like `ZL.A21` (real span:
2010-2010, one year) was still paying 56 rounds of multi-provider negotiation instead of
1-3. Real average station year-span in the key index is 5.55 years vs. a 56-year loop
every station was running -- ~10x average unnecessary overhead, worse for the many
short-deployment stations (all but 2 of 1984 stations span under 50 years). Fixed: the
loop now bounds itself to that station's own `[year_min, year_max]` from
`station_summary.csv` (+/-1 year margin), falling back to the full range only if a
station's summary entry is missing. Verified directly: `ZL.A21`'s job dropped from an
extrapolated ~500s to a real, measured 68s total.

**3. Preprocessing was memory-unsafe at full-history scale, independent of (1)/(2).**
The original design (`mdl_bluehive_quicktest.py`'s logic, ported as-is into
`orchestrator.py`) reads every downloaded file into one combined Stream, merges, then
processes the whole thing -- fine at 1-week scale, but for real full-history data this
used **7.3 GB RSS and got OOM-killed** on less than half a year of our single largest
station (`G.SSB`), before processing even started. Full history per station makes this
untenable at any reasonable memory allocation, not just an edge case.
**PI decision: day-by-day, matching the already-proven-safe design from
`preprocess_existing_archive.py`** (551MB RSS regardless of history length) --
*"compute time is expensive, this should be robust, even though it means more
read-write time... it is what it is."* `orchestrator.py`'s preprocessing was rewritten
to process and checkpoint **one calendar day at a time**, appending directly into the
per-station shard via a new shared `append_channel_data()` helper (extracted from
`build_master_h5.py`'s `merge_channel`, which now delegates to it -- the logger's
shard-to-master merge and the orchestrator's day-by-day checkpointing need the exact
same create-or-append-with-gap-fill logic, so it lives in one place now). A day-state
checkpoint file (`{shard}.daystate.json`) tracks completed days, and the shard HDF5 file
is only ever open for the duration of one day's append (not a station's whole run,
since HDF5 has no crash-safety against a hard kill mid-write). **Verified directly
against the real dataset that caused the original OOM** (925 real days, including a
genuine ~20-year calendar gap): peak RSS held flat at **377.6 MB throughout**, full
completion -- vs. 7.3GB that never finished. The 20-year gap itself exposed a fourth bug
along the way: the gap-fill wrote the *entire* gap as zeros in one array assignment
(633 million samples for that one gap) -- now chunked in bounded batches matching the
dataset's own chunk size.

**Checkpoint/resume verified under a real kill-and-restart, not just in isolation**: the
60-station canary batch was deliberately stopped mid-run (to swap in fix #2/#3 above)
and resubmitted against the *same* root. Every station's day-count survived untouched
(no resets), and the smaller stations resumed appending new days from exactly where
they'd stopped (e.g. `XA.SA01` continued 97 -> 102, `XE.EC01` continued 10 -> 11) within
minutes of the restart. Bigger, genuinely-decades-long stations (`II.NIL`, `YT.MRTP`)
took longer to resume checkpointing after a restart since download-phase year
negotiation is still a monolithic pre-step that must finish before that station's
day-loop resumes -- an accurate reflection of those stations' real size, not a stall,
but worth knowing if a future restart looks "stuck" on a big station.

**A real operational hazard, not a code bug**: Bluehive's SLURM controller was
observed briefly unresponsive (`sbatch`/`squeue` timing out cluster-wide) during this
session, and in that state a job can be **submitted successfully server-side while the
client-side `sbatch` call still reports an error** -- retrying blindly on an apparent
failure can silently create a duplicate array covering the same stations, racing on the
same shard files. Always re-check `squeue`/`sacct` for whether the job actually landed
before retrying a failed-looking submission.

**Real rate/ETA data, not yet a final campaign estimate**: early canary telemetry (33
concurrent tasks, ~85 min elapsed) measured ~23 KB/s per connection and ~2.76 GB/hr
aggregate -- the fix in bug #2 above should improve this substantially for the ~95% of
stations with short real deployment spans, but a trustworthy full-2,000-station ETA
needs a fresh rate measurement taken *after* that fix, not the pre-fix number. Revisit
once the current (post-fix) canary run has enough data-bearing stations completed to
give a clean read.

**Not yet done**: a full 2,000-station launch with all of the above fixes in place
(the 60-station canary is the current verification step for the combined fix set).
`master.py init` (no `--n-stations`) + `submit` once the canary confirms clean.

**Real per-station cost is heavily back-loaded toward recent years, confirmed by direct
investigation (2026-09-24, debug partition)**: `G.SSB` (the single largest station,
44-year real span) was still in its download phase after 3+ hours with zero checkpoints
-- worrying at first (this is the exact station that caused the original OOM), but a
real hang was ruled out directly: files were still accumulating steadily (15,640 files
over 4h33m real wall-clock, genuine progress). Isolated single-year download tests (one
each for 1990, 2005, 2020, run in parallel on `debug`) found the actual mechanism:
average file size grows sharply across eras -- ~150 KB/file (1990) vs. ~1.5 MB/file
(2005) vs. ~2.1 MB/file (2020), a ~14x difference. Modern broadband instrumentation
records at much higher native sample rates than the equipment a decades-old station
started with, and this pipeline downloads raw (undecimated) data before packaging -- so
a long-running station's most recent years cost far more download time than its early
years, independent of calendar-day count. Not a bug: real data volume, mechanistically
understood, not a hang or a hidden inefficiency. Implication for full-scale timing: the
~40 multi-decade outlier stations (mostly landing in `urseismo`'s work-balanced chunk)
will be more back-loaded/expensive than the current CPU-day model assumes, since that
model treats bytes/day as roughly uniform per station -- a real, not yet quantified,
upward revision to the full-campaign time estimate once more of these stations progress
further into their recent-year (expensive) data.

**`master.py progress` understated real progress by ~3x, fixed with a scale-aware
design worth remembering**: "packaged so far" only summed completed stations' result
JSONs, missing all in-progress stations' substantial checkpointed data (confirmed
directly: real on-disk packaged total was 34GB when the report said 10.94GB, because
big stations like `II.HOPE` at 8.7GB weren't done yet). Fixed to scan `packaged_h5/`
live -- safe because that directory is bounded at one `.h5` + one `.daystate.json` per
station (<=2,000 files regardless of run size). Deliberately did **not** extend the
same live-scan treatment to downloaded-bytes (`scratch_work/`, raw SEED): a single
station's raw files can already number 10,000+, so at full scale that directory could
hold millions of files, and walking it on every check would risk real load on the
shared filesystem metadata server this session already saw misbehave under concurrent
pressure. General principle for any future addition to this framework's monitoring:
before adding a live-disk-scan metric, check whether the thing being scanned is bounded
by *station count* (safe) or by *data volume/history length* (not safe at 2,000-station
scale) -- `packaged_h5/` is the former, `scratch_work/` is the latter.

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

## Full 2,000-station production launch (2026-09-24)

**Two-tier deployment strategy (PI-directed)**: rather than running all 2,000 stations as
one work-balanced split, `master.py init` now separates stations into a **fast tier**
(1,957 stations, `total_days <= 8000`, sorted smallest-first for fastest global-footprint
growth) and an **outlier tier** (43 stations, `total_days > 8000` — the same real cutoff
already visible in `station_summary.csv`), which runs **in parallel with**, not after, the
fast tier, exclusively on `urseismo`. Rationale: the ~40 genuinely multi-decade stations
are valuable but slow regardless of scheduling order (see the recent-years-cost-more
finding above) and only add incremental footprint/path-density per station, so sequencing
them after the fast tier would gain nothing and only delay them further.

**Carry-forward**: `master.py init --carry-forward-from <canary root>` migrates already-
`package_ok=True` canary results into the freshly re-initialized manifest under each
station's new idx (idx changes on re-sort) — the existing idempotency check in
`orchestrator.py` (skip if `prior.get("package_ok")`) then naturally recognizes and skips
them. No data files move: `packaged_h5/*.h5` and `*.daystate.json` are keyed by
`network.station`, not idx. 10 canary completions carried forward cleanly into the full
run.

**New bug found and fixed: `MaxArraySize` chunk-splitting.** The two-tier split produces a
much larger per-partition fast-tier chunk than the old single-tier split did (only 3
fast-tier partitions now share 1,957 stations, vs. 4 partitions sharing all 2,000 before).
The `standard` partition's chunk (idx 0-1775, size 1,776) exceeded Bluehive's
`MaxArraySize=1001` and failed with `sbatch: error: ... Invalid job array specification`.
The existing rebase-by-offset fix (from the earlier single-tier bug) only handled a chunk
that *starts* at a high index — it didn't handle a chunk that's simply too big, since
`offset = lo` is a no-op when `lo=0`. Fixed properly this time: `cmd_submit` now splits any
chunk whose size exceeds `MAX_ARRAY_INDEX` into multiple independent sub-array `sbatch`
calls (same partition/qos/walltime), each rebased to `0-(sub_size-1)` with its own true
starting row passed via `WAVENET_IDX_OFFSET`. Verified live: `standard`'s chunk correctly
split into two sub-arrays (idx 0-1000 and 1001-1775, jobs `31372579`/`31372580`), and the
full 2,000-station run is confirmed queued/running on Bluehive (`squeue`) across all 5
partition chunks plus logger/inspector on `urseismo`.

## Inspector silently never verified/purged anything (found + fixed 2026-09-24, hours into the full launch)

Caught by the PI noticing `verified+purged` staying at 0 in `master.py progress` while
`with data`/`merged` climbed normally — **not** visible from `squeue`/`sacct` alone, which
reported the inspector job as a clean `COMPLETED, exit 0`.

**Root cause**: `inspector.py` opens the single shared master HDF5 (`verify_station()`,
read mode) with no retry, but `logger.py` (the sole writer) briefly holds that exact same
file open during every station merge. Any inspector poll landing in that window hit
`BlockingIOError: unable to lock file` — a real, recurring race between two independent
30s-polling processes touching the same file, not a one-off. That exception propagated
all the way out of `main()` and killed the whole long-running service outright.

**Why `sacct` didn't catch it**: `inspector.slurm`'s retry wrapper (borrowed from
`orchestrator.slurm`, built for an unrelated transient shared-env import race at process
startup) retried the whole process 4 times, then simply reached the end of the script with
no explicit exit code — so SLURM reported `COMPLETED, exit 0` even though inspector never
verified a single station in ~2 minutes of real wall-clock time. A real lesson: a
"succeeded" SLURM state is not the same as "did its job" for a script that swallows
failures in a retry loop — always cross-check against the thing the service is actually
supposed to produce (here, `verified+purged` in `master.py progress`), not just job state.

**Fix**: new `lockutil.open_h5_retry()` retries an `h5py.File` open a few times with
backoff on `BlockingIOError`/`OSError`, used by both `inspector.py`'s `verify_station()`
(the reader) and `logger.py`'s own master-file open (the writer, for symmetric
protection against the same race from the other direction). `inspector.py`'s main loop
also now catches any residual per-station failure and retries it on the next poll cycle
instead of crashing the entire service over one station. Separately, the
orchestrator/logger/inspector SLURM templates now `exit 1` after a genuinely exhausted
retry loop instead of silently falling through to exit 0, so a real unrecoverable failure
is visible as `FAILED` in `sacct` going forward, not indistinguishable from success.

**Verified, not just patched and hoped**: a standalone lock-contention test (one process
holds a write-lock for 4s, another calls `open_h5_retry` for read) confirmed the retry
recovers once the writer releases the file. Resubmitted inspector directly against the
live production run (without touching the already-running orchestrator array or logger)
and confirmed `verified+purged` climbing for real (0 -> 26+ within about a minute) via
`master.py progress`.

Last updated: 2026-09-24 (full 2,000-station production run launched: two-tier
fast/outlier split, canary carry-forward, a MaxArraySize chunk-splitting bug, and an
inspector HDF5-lock-race bug — all found and fixed the same session, each verified live
against the real running deployment rather than assumed from a clean exit/job state).
