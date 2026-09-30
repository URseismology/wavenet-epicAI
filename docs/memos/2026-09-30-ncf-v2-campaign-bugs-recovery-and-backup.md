Subject: NCF Campaign — v1→v2 Bug Fixes, Recovery Verification, Backup Plan, and Tasks for wavenet_junior + wavenet_senior

Hi team,

Full technical detail lives in `docs/ncf_pipeline_stages/2026-09-30_HANDOFF_pre_pruning.md`
(the authoritative record — read it before touching any of the systems below) and
`docs/ncf_pipeline_stages/PROGRESS.md` (live operational snapshot). This memo is
structured the same way as the last one: TL;DR, your actual tasks, then end-notes with
the full evidence. Read the first two, use the third for follow-up.

This is entirely the NCF real-waveform acquisition workstream (BlueHive3 +
terravibranium). It has no bearing on and shares no code with the FTAN/U-Net ML
pipeline — I mention this explicitly because it's easy to conflate the two when
"terravibranium" comes up in both contexts.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TL;DR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  □ I found that our download config had silently dropped data for 954 of our 2,000
    stations (location codes and channel bands we were never requesting), plus another
    511 tasks getting OOM-killed at too tight a memory limit. Fixed both, and — this
    matters — VERIFIED on BH3 with a real download test that the fixes actually recover
    data (25-33% measured recovery, zero regressions), not just inferred from metadata.
    Detail: End-notes A.
  □ Found and fixed a genuine replacement for 108 of 208 stations that had no
    seismometer at all (magnetometers, pressure sensors, accelerometers instead). One
    station in our locked 2,000 turned out to be the InSight lander — on Mars. Deleted,
    not replaced. Detail: End-notes B.
  □ v2 is the live re-run of everything the above broke (1,339 stations). v1 is the old,
    superseded run — don't touch it, it's winding down. Detail: End-notes C.
  □ Found and fixed a silent bug in the scratch-space purging process (it was silently
    dying after one cycle) while scratch was sitting at 80% of its hard limit. Detail:
    End-notes D.
  □ Started mirroring packaged output to all three required backup destinations
    (repovibranium, atos, terravibranium) for the first time — currently a one-time
    snapshot, needs to become a recurring job. Detail: End-notes E.
  □ Everything above is committed to `main` (`4dd8859`).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR TASKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TASK (wavenet_junior) — Full ownership of tracking the v2 run

  You have full command of this, not just watch-and-report. Run this to check state:

     ssh bluehive3
     source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/conda.sh && conda activate instaseis
     python3 /scratch/tolugboj_lab/wavenet_ncf_framework/production/master.py progress --root /scratch/tolugboj_lab/wavenet_ncf_production_v2

  It prints station count, days checkpointed, GB downloaded/packaged, and whether the
  purge process is actually running (watch for "inspector chain: OK" — if it ever says
  NOT RUNNING, that's the exact bug I describe in End-notes D recurring; message me).

  v1 vs v2, so the queue doesn't confuse you: `wavenet_ncf_production` (v1) is the old
  run, carrying the bugs in End-notes A — it's winding down, don't touch it. `_v2` is
  current and live, built with `--carry-forward-from` v1 so we don't redownload the 668
  stations that already succeeded there. One display quirk: `progress`'s job list is
  for my whole account, not scoped to the root you point it at — if you see unfamiliar
  job IDs while checking v1, that's normal.

  The hard-link rule — do not violate this: the 668 carried-forward stations' `.h5`
  files in v2 are hard links to the same files in v1, not copies (this saved us
  re-downloading them — see End-notes C for why that needed fixing in the first place).
  Same file on disk under two names. **Never edit or reprocess those files in place** —
  a rewrite in either root affects both. Copy out first if you ever need to touch one.

  Ongoing: track progress daily, flag if the ETA balloons or quota numbers look wrong,
  and report anything that looks off once you start sampling the packaged output.

TASK (wavenet_senior) — Start exploring the data structure, in parallel with U-Net work

  Structure: one HDF5 per station, `<network>.<station>.h5`. One dataset per channel
  (e.g. `LHZ`), a single float32 array on an absolute time grid — sample `i` sits at
  `epoch + i/sampling_rate` (epoch stored in the dataset's own `epoch` attr, default
  1970-01-01). Two stations' channels are already time-aligned if you index the same
  range on both — deliberate design, no offset table needed. Each dataset carries
  `sampling_rate` and `units` (`m` if response removal succeeded, `counts` if not —
  check per channel). A companion `_coverage/<channel>` bitmap (one byte per day) tells
  you which days are real data versus sparse zero-fill.

  The gap you'll hit: this is single-station preprocessed waveform, not a
  cross-correlation function yet — I haven't built that step. To get to an FTAN image:
  pick a station pair, pull aligned windows using the grid above, compute the actual
  NCF yourself (or build that step), then run FTAN. Our existing FTAN/U-Net code is in
  `src/wavenet_pipeline/03_machine_learning/` (`ftan_grid.py`, `validate_ftan.py`) — it
  currently expects CPS-synthetic CCFs, so check its exact input contract before
  assuming real NCFs slot in unchanged.

  Goal once this is working: an independent, real-world check on the trained U-Net that
  synthetic CPS data alone can't give us.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
END-NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

A. Config and memory bugs, found and VERIFIED fixed

  Of 2,000 manifest stations, 954 (47.7%) had returned zero bytes from the v1 campaign.
  I audited all 954 against live FDSN metadata:

    433  had BH/LH registered but got nothing anyway
    312  had a real seismometer, just not in our requested band list
    208  had no seismometer at the site at all (handled separately, see B)
      1  manifest defect (empty network code)

  Root causes, both in `production/orchestrator.py`:
    1. `location_priorities` was hardcoded to `["", "00", "10"]`. In a 25-station
       sample, 10 (40%) had ALL their location codes outside that set (01, 02, 30, 31,
       32 observed — usually multiple sensors at different depths at one site, not
       junk). Fixed to `"*"`.
    2. `CHANNELS` defaulted to `"BH?,LH?"` only. Fixed to
       `"LH?,MH?,BH?,SH?,HH?,EH?"` — every high-gain seismometer band >=1 Hz
       (Nyquist floor for our 0.4 Hz lowpass), cheapest first.
    3. `--mem-per-cpu=2G` against nodes whose real per-core share is 7.6-9.0 GB — 511 of
       2,002 tasks were OOM-killed with MaxRSS pinned at the ceiling. Fixed to 7G
       (deliberately not `--mem=0`, which would drop concurrency to 1 task/node).

  I do not trust metadata alone for "this should work now" — that exact assumption is
  what produced this bug. So I ran a real `MassDownloader` test on BH3 itself (has to
  run there, not terravibranium — more on that below) against sampled stations from
  both categories:

    has_BH_LH_should_have_worked   : 12 tested, 8 already worked, 3 MORE recovered (25%)
    missed_other_seismometer_band  : 12 tested, 0 already worked, 4 recovered (33%)
    0 errors, 0 regressions (every station that worked before still works)

  Real example: `XG.ERGN` went from 0 bytes to 15.7 MB of BH+LH data. `BC.SPX` went from
  0 to 41 MB of HH data.

  One mistake I made and caught: my first attempt at this verification ran on
  terravibranium and reported 0/20 recovered across the board. That was wrong — on a
  machine saturated with our other packaging workers, `MassDownloader` couldn't even
  construct itself (a Python thread-limit error), and a bug in my own test script
  silently swallowed that error instead of surfacing it. Re-ran correctly on BH3 and got
  the real numbers above. Lesson for anyone touching this again: any test that exercises
  FDSN/MassDownloader has to run on BH3, never terravibranium.

B. 208 no-seismometer stations — 108 replaced, 1 deleted, 99 still open

  Selection rule: candidate must be a real high-gain seismometer (not a naive
  "lowest sample rate" pick — I found 794 sub-1Hz channels in our station set that are
  mass-position telemetry, clock signals, and electric-potential sensors, not ground
  motion, and a naive rule would have picked those). Distance to the original station is
  the primary criterion (nearest wins); sampling rate only decides which band to use at
  a given station. Acceptance threshold: 0.5 degrees. Data availability is a hard gate,
  verified with real `get_waveforms` calls, not metadata — my first pass at this had a
  39% false-negative rate (checking the wrong time window), caught and corrected before
  finalizing.

  `XB.ELYH0` (one of our locked 2,000) is the InSight lander on Mars. Deleted outright.

  Final: 108 accepted with confirmed data, 1 deleted, 99 stations where no acceptable
  replacement exists (recorded, not silently dropped — see
  `fps_stations_v2.csv`/`channel_audit_2026_09_30/` for the full detail and reasoning
  per station).

C. v1 → v2: what changed, and a data-loss bug I found while setting it up

  v2 is the same 1,999-station manifest (108 replacements + the deletion applied) run
  under all the fixes in A, using `--carry-forward-from` v1 so the 668 already-successful
  stations there are skipped rather than redownloaded.

  While setting this up I found that `--carry-forward-from` copies the SUCCESS RECORDS
  but not the actual data files — v2's packaged-data directory was completely empty
  while claiming 668 stations complete. Fixed by hard-linking the real files in from v1
  (zero extra disk cost, same filesystem) — see the junior task above for the one rule
  that comes with this.

  A worse version of the same problem: 8 of those 668 "successful" stations had real
  downloaded bytes (184 MB up to 12.9 GB each, ~22.6 GB total) but NO actual packaged
  file anywhere — lost at some point in an earlier scratch reorganization. Caught before
  v2 could inherit and permanently hide this; those 8 are re-running now.

  Currently: 1,339 stations being re-run (837 recovered by the config fix, 386 by the
  memory fix, 108 new replacements, 8 the data-loss catch above). ~96% reported, 1,018
  with real data confirmed so far.

D. Scratch-purge process silently stopped — found and fixed today

  The process that frees BH3 scratch space by purging verified-complete raw data ran
  once, then silently stopped resubmitting itself — a path issue in the job script that
  failed into a side log instead of showing up as a visible job error. This mattered
  because scratch was at 80% of its hard limit with nothing purging. Found it while
  writing up today's work, fixed the root cause (not just restarted it), confirmed the
  fix holds (it's chained through two cycles since).

E. Backup / mirror to repovibranium, atos, terravibranium — started, not yet automated

  Per standing instruction: all packaged shards mirrored to all three, no distinction
  between which campaign run produced them; a merged master built on terravibranium
  once the campaign completes, backed up to the other two; documentation stating this is
  preprocessed waveform data, not the finished NCF product. The first two are
  in progress; the third (merged master) is correctly deferred to campaign completion.

  Mirror scripts are committed at
  `src/wavenet_pipeline/00_waveform_acquisition/archive_tools/mirror_to_{repo,atos,terra}.sh`
  — separate from `archive_move.py`, which is a different tool (move-and-delete for
  stale data, not copy-and-keep for live campaign output; see that directory's own
  README for the distinction). **Open item**: each mirror script snapshots its file list
  once and stops — it does not pick up stations that finish afterward. This needs either
  a cron entry or a standing habit of re-running it; not yet automated.

F. Size, compute cost, and research value

  Projected final size, computed from the current packaged-bytes-per-checkpointed-day
  ratio (0.988 MB/day) against the full manifest's expected day count (2,739,232 days):
  roughly 2.7 TB packaged once complete. An earlier, independent projection in
  `PROGRESS.md` put this at "~3TB, likely a floor not a ceiling" — consistent with this
  live-computed number; I'd treat 2.7-3.5 TB as the realistic range rather than a single
  point estimate. Raw downloaded data is several times this (~4 TB downloaded so far
  against ~500 GB packaged), but does not accumulate — the purge process in D removes
  verified raw data continuously, so the steady-state footprint is the packaged total
  above, not the raw total.

  Compute cost: effectively $0 direct cost. Data acquisition is over the free public
  FDSN protocol (we deliberately did not use the AWS zero-egress path documented in
  `stage_1_data_availability_cost_analysis.md`, which has small real dollar costs); BH3
  and legacy BlueHive are CIRC-allocated resources under `tolugboj_lab` with no SU/dollar
  billing model in effect for this project (unlike Empire AI, where billing starts
  2026-10-01 and is unrelated to this campaign). The real cost is shared cluster
  node-hours on a resource other groups also use, not anything invoiced to us.

  Research value: this is real, globally-distributed ambient-noise waveform data,
  independent of our CPS-synthetic training set. Two concrete uses once the NCF/FTAN
  step is built: (1) an out-of-distribution validation set for the trained U-Net that
  synthetic data structurally cannot provide, and (2) per CLAUDE.md's stated project
  goals, a shared benchmark dataset for comparing this repo's classical U-Net approach
  against the AkiNet/iRADNet physics-informed frameworks in the broader Empire AI
  research program.

— Tolu
