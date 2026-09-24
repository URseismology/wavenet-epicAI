Subject: NCF Waveform Pipeline — Real Correctness Verification + ObsPy Scaling Numbers + Next Task for wavenet_junior

Hi team (wavenet_junior, this one's mainly for you — everyone else, the ObsPy scaling
numbers below feed directly into the AWS-vs-ObsPy cost report once the mothership scan
lands),

Full technical detail lives in docs/ncf_pipeline_stages/ (PROGRESS.md is the entry
point, hdf5_schema.md and HANDOFF_wavenet_junior.md have the code-level detail). This
memo is the "here's what's real, here's what I want you to do next" version.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT'S NOW REAL, NOT JUST PLAUSIBLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

We picked ObsPy's MassDownloader over ROVER (ROVER's high-level commands depend on a
now-retired FDSN service; MassDownloader doesn't, and it fetches StationXML for free)
and Bluehive as the execution home (its `urseismo` partition — 120 cores, exclusive to
us, confirmed zero queue wait even at 20-way concurrency).

The one gap that mattered most — is our instrument-response removal actually correct,
not just non-crashing — is now closed with a real, independent check, not a plausibility
argument:

  I found ADAMA's own already-processed output for GT.BOSA (a real station, part of the
  GT network, on terravibranium: /RAID6/bluehiveBackup/Prj5_HarnomicRFTraces/2_Data/
  preprocessed_data/DataGT/BOSA/) — SAC files independently produced by a different
  pipeline than ours. I ran our own preprocessing against the same raw day (1993-058,
  pulled from a real archive on `atos`, a machine that turned out to hold a large
  backup of old Bluehive project data) and compared numerically:

    min/max agree to 4-5 significant figures
    amplitude std ratio: 0.9998
    correlation coefficient: 0.9523

  Two independently-built pipelines, same raw instrument response, near-identical
  displacement output. That's real evidence, and I want it independently reproduced,
  not taken on my word — see your task below.

Also real, not projected: a 27-year single-station stress test (II.EFI, 1,875 real
days, zero download cost since the raw data already existed on Bluehive) surfaced two
things a short toy test never would have:
  - Non-standard channel orientations are real (one channel's true azimuth was 343°,
    not aligned to north at all) — confirms the azimuth/dip metadata we now store per
    channel is load-bearing, not over-engineering.
  - A subtle one: real instrument sample rates aren't exactly the nominal value
    (0.999999713897705 Hz, not 1.0) — over 27 years that compounds to an estimated
    ~243 seconds of time drift if uncorrected. Flagged in hdf5_schema.md, not yet
    resolved — worth keeping in mind for anything doing precise multi-decade alignment.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OBSPY / BLUEHIVE TIME, STORAGE & SCALING NUMBERS (for the record, feeds the cost report)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

All confirmed by direct testing (single-station runs, a 20-task SLURM array, and the
27-year stress test above) — not projections:

  Preprocessing (the real bottleneck, not download):
    ~5-6 s/channel-day    when a channel is already at our 1 Hz target rate (`LH?`)
    ~9-16 s/channel-day   when only `BH?` exists and needs decimating from a much
                          higher native rate (real station-to-station variance seen:
                          9.1s for one station, 16.1s for another, both legitimate)

  Storage: ~300-410 KB/channel-day (real range across different stations/content, not
  a single fixed number) — essentially flat regardless of channel type or concurrency
  level, since final output is always decimated to 1 Hz. gzip-4 compression only saves
  ~7% on this data (it's high-entropy after filtering) — if storage ever becomes a real
  concern at full network scale, the lever is int16 quantization or a coarser rate, not
  compression tuning.

  Scaling: linear from 1-way to 20-way concurrency on `urseismo` — no measurable
  slowdown from provider rate-limiting or shared-filesystem contention at that scale.
  20 tasks all entered RUNNING immediately, zero queue wait.

  The real-archive stress test also reinforces why raw SEED must be discarded after
  packaging, not kept: /RAID6/Prj_terraSeis/output/ on terravibranium is 2.5 TB of kept
  raw SEED for a regional project; PrjXX_SAmericaNoise's own raw archive on Bluehive
  scratch is 11 TB. Real numbers, not a hypothetical risk.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCALE PREVIEW: WHAT THE 2000-STATION PROBLEM MIGHT COST (summary, not final analysis)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

We haven't chosen a scope yet (how many days per station, full history vs. a bounded
window) — that choice is still open, pending the mothership AWS-side numbers. This is
just to get everyone's head around the scale before that choice gets made, using the
confirmed per-channel-day numbers above plus the still-running 27-year `II.EFI` test as
a real reference point. Round numbers, illustrative — not the detailed cost report.

Assumptions used (middle of our confirmed real ranges): ~5 channels/station, ~10 s/
channel-day preprocessing, ~350 KB/channel-day storage. 2,000 stations fixed.

| Scenario | Channel-days | Serial time | Time on `urseismo` (120-way parallel) | Storage |
|---|---|---|---|---|
| 1 week/station (current test scale) | 70,000 | ~8.1 days | **~1.6 hours** | ~24.5 GB |
| 1 year/station | 3,650,000 | ~422 days | **~3.5 days** | ~1.28 TB |
| Full multi-decade history/station (using `II.EFI`'s real 1,875-day/27-year span as an illustrative upper bound — not every station has this much history) | 18,750,000 | ~5.9 years | **~18 days** | ~6.6 TB |

Two things this table is actually saying:
  1. **Parallelization is what makes any of this tractable at all** — the difference
     between "5.9 years" and "18 days" is entirely `urseismo`'s 120 cores, confirmed
     linear at least up to 20-way concurrency this session (not yet confirmed all the
     way to 120-way — a real assumption in this table, not proven).
  2. **Scope choice matters enormously.** Full-history-per-station is a ~270x larger
     problem than a 1-week bound, in both time and storage. This is exactly why the
     North Star minimum-coverage metric (PROGRESS.md) exists — bounding to "just enough
     days for every station to have one valid connection" should land much closer to
     the 1-week/1-year rows than the full-history row.

Memory: comfortable throughout, real number not just an assumption — checked the
27-year `II.EFI` job directly while it was ~1 hour in (hundreds of days already
processed): **551 MB RSS**, confirming the day-by-day processing design (never holds
more than one day's data across all channels at once) keeps memory low regardless of
how many years of history a station has. The one still-unconfirmed point: the final
concatenation step (building one array per channel from all ~1,875 days at once)
happens only once at the very end and is estimated at ~1.9 GB for 3 channels — not yet
observed since the job hasn't reached that point, but comfortably within the 8 GB
requested.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT I WANT FROM YOU, wavenet_junior — TWO TASKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

□ TASK 1 — Reproduce the GT.BOSA correctness check yourself, independently

  Don't just read my numbers above and move on. To make this easy — no `atos` access
  needed — I staged exactly what you need on repovibranium (same access you already
  have per the team onboarding memo):
    /volume1/web/Wavenet/NCF_Verification/
      gt_bosa_days_057-060_raw.tar       -- raw SEED, GT.BOSA, 1993 days 057-060 (39 MB)
      bosa_reference_057-060.tar          -- matching ADAMA reference SAC (2.7 MB)
      README.md                            -- exact steps
    Our code:  src/wavenet_pipeline/00_waveform_acquisition/rover_download/
               preprocess_existing_archive.py (produces the packaged output) and
               compare_against_reference.py (the comparison approach — hardcoded to
               this exact run, adapt the paths/dates, it's a worked example not a CLI
               tool).

  (Repovibranium's `scp`/`sftp` doesn't work either, same Synology quirk as `atos` —
  use `ssh repovibranium "cat file" > local_file`, or just browse/download via the web
  share if that's easier for you.)

  Get your own correlation/amplitude numbers. If they don't match mine, that
  discrepancy matters more than either of us assuming the other is right.

□ TASK 2 — New, paper-motivated follow-up: XD.MTAN / XD.RUNG, channel BHZ

  These two stations are the ones from our paper — a real, scientifically-motivated
  pair, not an arbitrary one. Good news: you do NOT need `atos` access for this one.
  I already confirmed both are fully available through the same public FDSN service
  our MassDownloader pipeline already uses:
    XD.MTAN: -7.9073, 33.3203, deployed 1994-05-25 to 1994-10-30
    XD.RUNG: -6.9372, 33.5180, deployed 1994-05-25 to 1995-05-16
    ~110 km apart, overlapping window 1994-05-25 to 1994-10-30 (~5 months) — real
    geometry, not just structurally present.

  Use the existing, already-proven MassDownloader pattern (mdl_bluehive_quicktest.py)
  pointed at network="XD", station="MTAN" and "RUNG", channel="BH?" (this 1994
  deployment predates the LH-preference optimization's relevance — BHZ specifically is
  what's wanted here), over the overlapping window above. This is a download-based
  test, not the download-free archive-replay style of Task 1 — expect real
  download/preprocess time per the numbers above.

  Once you have both stations packaged and loaded (build_master_h5.py + 
  load_master_h5.py, same as the rest of this pipeline), the actual ask: do the results
  look sane heading into NCF cross-correlation? Confirm both series cover the same real
  calendar range, then try ObsPy's own obspy.signal.cross_correlation.correlate on the
  two loaded arrays as a basic smoke test — full NCF (Stage 5) doesn't exist in this
  pipeline yet, so this is a readiness check, not a claim that Stage 5 is built.

  One more thing worth checking yourself, not something I've verified: there may
  already be processed SAC data for XD.RUNG on terravibranium under
  /RAID6/bluehiveBackup/Prj12_AfrTxRF/2_Data/SAC/XD/RUNG/ — I haven't confirmed whether
  those are continuous-day traces (comparable to our output) or event-cut receiver-
  function segments (a different processing purpose, not directly comparable) — worth
  a quick look before assuming it's a second trusted reference the way GT.BOSA was.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO REPORT BACK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ Update docs/ncf_pipeline_stages/PROGRESS.md and the relevant stage doc's Hardware
    Tier Log the same way this session's work is already tracked — same convention,
    just your name on the tier row instead of mine.
  ✓ "I reproduced it, same numbers, no concerns" is a completely valid report.
  ✓ If your correctness-check numbers differ from mine, or the XD pair doesn't look
    NCF-ready for a reason I haven't anticipated, say so plainly — that's exactly what
    this task is for.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UPDATE 2026-09-24: FULL-HISTORY PRODUCTION DEPLOYMENT, A REAL BUG, AND A NEW ROLE FOR YOU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

wavenet_junior — this section is the reason I want you doing more than Tasks 1/2 above
going forward. Short version: we moved to real full-scale deployment, found a real bug
the hard way, and it's exactly the kind of thing a second set of eyes watching the run
(not just building it) would have caught faster. Read on for the concrete ask.

WHAT CHANGED: FULL HISTORY, NOT A BOUNDED WINDOW

Decision (2026-09-24): download all available data for every station, not a
connectivity-gated subset — "the goal is to get all the data." That settles the
scope-choice question Task 1/2's memo left open above. Real numbers from the key index
once this was decided: **78.9 TB total**, spanning from a few dozen days up to **56
years** for the longest-running stations (median far smaller — most of that volume and
time sits in a long tail of ~40 outlier stations).

A production framework now exists to actually run this at scale — master/orchestrator/
logger/inspector, `src/wavenet_pipeline/00_waveform_acquisition/production/`, full
detail in `docs/ncf_pipeline_stages/PROGRESS.md`'s "Production framework" section:
  - `orchestrator.py` — one SLURM array task per station (download -> preprocess ->
    package), idempotent so a resubmit doesn't redo finished work
  - `logger.py` — the single process that merges finished stations into the shared
    master HDF5
  - `inspector.py` — independently re-verifies each merge, only THEN deletes the raw
    SEED for that station
  - `master.py` — `init`/`submit`/`status`/`progress`/`stop` CLI, splits the array
    across `urseismo`/`standard`/`preempt`/`interactive` by estimated WORK (not station
    count — the biggest ~11% of stations carry ~78% of total cost, so an equal split
    would leave three partitions idle for days while one was still grinding)

Real infra bugs found and fixed while building this (all in PROGRESS.md if you want the
detail): a conda double-activation race that broke `pkg_resources` under `sbatch`, a
duplicate-processing race from an orphaned test job, an inspector exit-condition bug,
and a SLURM `MaxArraySize` limit that rejected two of four partition chunks outright.
All fixed and verified on a real 15-station end-to-end run before touching the full
2,000.

WHAT HAPPENED AT FULL SCALE (the part that matters for you)

I launched all 2,000 stations today. At ~800 stations reported, **zero** had
successfully packaged. Digging in: ObsPy's MassDownloader has a real internal bug —
querying IRIS's "availability" endpoint over a many-decade span sometimes crashes
outright (`TypeError: sequence item 0: expected str instance, tuple found`, ~28% of
completions) and sometimes gets silently swallowed and reported as "IRIS has no data"
(the rest) — meaning a real chunk of that 0% wasn't stations genuinely lacking data,
it was us silently losing IRIS-hosted data without any error at all. I've cancelled the
run and am testing a fix (splitting each station's request into yearly sub-windows,
which every prior test already proves is safe — the bug only shows up on very wide
single requests).

Why this matters beyond "we found a bug": every test I ran before today's full-history
switch used a 1-week window (see the "OBSPY / BLUEHIVE" numbers above) — none of that
testing could have exposed a bug that only triggers on multi-decade spans. What I DID
check before launching — watching one station start downloading correctly — proved the
mechanism worked, not that it worked at scale across many real stations and providers.
That gap is the actual lesson: a live production rollout needs someone watching
*aggregate* behavior (success rates, error patterns) while it runs, not just a
pre-launch spot check. That's a distinct job from building the pipeline, and it's one
I was doing badly single-handedly, because I was heads-down on the fix, not the watch.

YOUR ROLE, GOING FORWARD: PRODUCTION TESTER, NOT JUST A ONE-OFF VERIFIER

This builds on what you're already doing (Tasks 1/2), formalized as an ongoing role,
using standard software-testing practice rather than ad hoc checking:

  1. SMOKE TEST (before I hand you a run to watch) — I'll always confirm a run starts
     without immediate crashes before you get involved; that's on me, not a new ask.

  2. LIVE MONITORING DURING ROLLOUT (this is the new, important one) — while a
     production run is active, periodically run
       `python3 .../production/master.py progress --root <ROOT>`
     and watch for anything outside expected range, not just "did it crash":
       - success rate ("with real data" / "orchestrator tasks reported") dropping to
         near-zero or swinging wildly is a red flag on its own, independent of any
         error message — that's exactly the signal that would have caught today's bug
         hours earlier than I did
       - error messages repeating identically across many different stations (like
         today's TypeError) means an infrastructure/code bug, not per-station data
         absence — worth flagging immediately, not batching into a later report
       - `squeue` showing a partition's tasks stuck PENDING far longer than expected,
         or a partition's tasks all finishing suspiciously fast (usually means they're
         all failing fast, not succeeding fast)

  3. DATA QUALITY SPOT-CHECKS (extends what Task 1 already has you doing) — once a run
     is producing real merged stations, periodically pull a small random sample from
     the growing master HDF5 and sanity-check it the way you'll already know how to
     from Task 1: non-empty, no all-zero/NaN channels, plausible amplitude range,
     `units` attr matches what you'd expect. This is sampling-based QA, not a full
     re-verification of every station — the inspector already does an automated version
     of this per-station; your spot checks are the independent human check on top of it.

  4. STRUCTURED BUG REPORTS — when something looks wrong, capture it so it's actionable
     without back-and-forth: which station(s) (network.station, or the idx from
     results/*.json), the exact error text if there is one, roughly how many other
     stations show the same pattern, and what you were running when you saw it. "Several
     stations near idx 500-600 are all failing with the same TypeError" is immediately
     useful; "downloads look broken" isn't.

  5. CANARY BEFORE FULL SCALE — for the relaunch (and any future one): once my
     year-chunking fix is verified on the specific stations that broke today, I'm going
     to relaunch a small canary batch first (a few dozen stations spread across all four
     partitions, including some of the multi-decade ones that triggered today's bug) and
     let it run for a defined window before scaling to the rest — not jump straight back
     to all 2,000. I'd like you watching that canary window specifically: confirm the
     success rate looks sane and no repeated error pattern shows up before I scale
     further. This is standard practice for any risky rollout (canary/phased release) —
     we're applying it here because today is the second time this project has learned
     that lesson the expensive way.

WHAT I NEED FROM YOU NOW

  □ Read this section and PROGRESS.md's "Production framework" section so the pieces
    (orchestrator/logger/inspector/master) aren't a black box to you.
  □ When I relaunch the canary batch (I'll ping the team), run `master.py progress`
    against it every hour or so for the first few hours and report what you see —
    including "looks fine, success rate matches expectations" as a valid report, same
    as Task 1/2 above.
  □ Keep Tasks 1 and 2 above on your plate too — this doesn't replace them, it's the
    same kind of independent-verification instinct applied to a live system instead of
    a one-off check.

— Tolu
