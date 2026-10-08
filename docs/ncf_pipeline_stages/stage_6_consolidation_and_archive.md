━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE 6 — CONSOLIDATION AND ARCHIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**This is a DESIGN, not a result.** Nothing here has been implemented. PI direction
2026-10-08: "for now plan. Later test, verify, then deploy."

HYPOTHESIS

  Three independently-produced packaged datasets (v1, v2, and the terravibranium SAmer
  campaign) can be merged into ONE self-describing HDF5 shard per station, plus a
  redundant master index for cheap crawling, without losing a single packaged
  channel-day and without silently admitting a defective one.

  Two claims, and the second is the hard one. Merging is easy; merging while keeping a
  known-bad day out of the product AND on the record is where this can fail.

SETUP

  Measured 2026-10-08, not assumed:

  | source | stations | size | state |
  |---|---:|---:|---|
  | v1  `/scratch/tolugboj_lab/wavenet_ncf_production/packaged_h5`     | 1,090 | 716 GB | quiescent |
  | v2  `/scratch/tolugboj_lab/wavenet_ncf_production_v2/packaged_h5`  | 1,513 | 545 GB | ACTIVELY WRITING |
  | SAmer  terravibranium `/RAID6/lab_archive/packaging_results` + shards | 889 | 424 GB | 7 stragglers |
  | total | | ~1.7 TB | |

  Destination: **atos** `/volume1` — Synology DS1525+, 42 TB total, 516 GB used.
  Write + checksum + delete verified from axon-1. Reachable from BlueHive3 **compute
  nodes**, not only the login node (TCP check from allocated node `bhd0099`), so the
  transfer needs no relay through terravibranium.

  Station-set overlap, measured:
  * SAmer vs the locked 2,000-station FPS manifest: **101 overlap, 788 are new.**
    The SAmer set is therefore not a subset — it is a regional augmentation.
  * v1 vs v2 draw on the same manifest, so most stations exist in both with
    different day sets.

WHAT WAS TRIED (facts established before designing)

  * **An archive already exists on atos** and is stale:
    `/volume1/NetBackup/wavenet_archive/packaged_h5_ncf/` — 1,013 shards, 451 GB,
    2026-09-30. It predates STEP 4's 293 rebuilt stations (+225,144 days), v2's growth
    to 1,513 shards, R-1, and all of SAmer. It must not be treated as current, and the
    consolidated product must not be written over it.
  * **Its promised provenance file was never written.** The README states per-file
    campaign provenance lives in `MANIFEST.md` "in this directory"; `find` returns
    nothing. The archive records WHICH shards it holds, not WHICH CAMPAIGN produced
    each. This is the gap the master index closes, and it is a worked example of
    §7 — verify the artifact, not the label.
  * atos's four USB shares (13 TB each) are 97–100% full and are not ours. `/volume1`
    only.

DESIGN

  ── Ordering principle (REVISED 2026-10-08 after PI direction + measurement) ──────

  **The archive carries only clean, usable data** (PI). It is a curated product, not a
  dump. Earlier drafts of this document said "archive the originals first", which
  conflated two different requirements:

    RETAIN   keep a recoverable copy of the inputs until the merged product verifies.
             Satisfied on scratch/RAID6. Costs nothing, ships nothing.
    ARCHIVE  write a curated artifact to atos for other people and agents to use.
             Must be clean.

  Only RETAIN is needed for reversibility. So: **originals are retained in place, never
  deleted until P7 passes; only the clean consolidated product is archived.** Scratch has
  553 TB free against our 1.7 TB, so retention is not under pressure.

  ── Phase ordering, with the issue work interleaved ───────────────────────────────

    P1  Issue fixes: isolated test per issue.            <- see CAMPAIGN_ISSUES.md
    P2  ONE integration test of the combined fix set.
    P3  ONE deploy + completeness campaign.
    P4  Consistency check on the 101 overlap stations.
    P4b OFFLINE RESPONSE REPAIR (R-19) -- no provider contact, no re-download.
    P5  Consolidation (test -> verify -> deploy).
    P6  Master index.
    P7  Verify consolidated against the RETAINED originals.
    P8  Archive the clean product to atos; rebuild JSONs from it.
    P9  Retire the 2026-09-30 atos archive, with a note recording why.
    P10 Hand to the delivered-network analysis agent (Stage 5 re-run).

  The old P0 (archive originals immediately) is **deleted**. Consolidation stays after
  the completeness campaign: doing it before means doing it twice, and only the second
  pass would count.

  P9 retires the old archive **after P7 verification passes, not at upload time**.
  Uploaded is not verified, and the old archive is the only other copy on atos.

  ── The merge rule ────────────────────────────────────────────────────────────────

  Unit of merge is the **channel-day**, not the station and not the file. The absolute
  1970-anchored day grid already shared by the v2 schema makes this an index operation
  rather than a time alignment.

  Precedence, per channel-day present in more than one source:

    1. **Quality first, campaign second.** Prefer the copy that passes the admission
       predicate. Only if both copies are equally clean does campaign order decide.
       The earlier draft said flatly "v2 over v1" on the assumption that v1 was the
       pre-fix campaign. **The measurement says otherwise: v1 is 97.23% clean and v2
       is 95.51%, with `patch_level` 3 on every shard in both.** A blanket v2>v1 rule
       would systematically prefer the dirtier copy.
    2. **SAmer contributes its 788 non-manifest stations ONLY** (PI, 2026-10-08: same
       directory, "no overlaps with original manifest"). The 101 stations SAmer shares
       with the FPS manifest are dropped from the SAmer side entirely — FPS is
       authoritative for them. There is therefore NO day-level merge between SAmer and
       the FPS campaigns, which removes a whole class of merge risk. The P4 consistency
       check survives only as a cheap diagnostic on those 101, no longer as a gate.
    3. A source contributes a day only if NOTHING else holds it.

  ── Admission predicate (PI, 2026-10-08) ──────────────────────────────────────────

  **Clean means UNITS only.** A channel-day is admitted iff `units == "m"`, after the
  P4b repair pass.

  Explicitly NOT grounds for exclusion:
  * **Sampling-rate jitter** (R-7) — 1.00806 Hz vs 1.0, 60 channels in v1 and 71 in v2.
    PI: "little jitters in sampling rate are okay since they can be corrected easily in
    post processing." Admitted; the true rate is recorded per channel in the shard so
    post-processing can correct it.
  * **QC flags** (R-6) — the per-day sidecar records `dead`, `railed`, `saturated`,
    `n_segments`, `rms`, `ptp`, `robust_sigma` per channel-day. These are ADVISORY
    metadata, not a verdict: the day is packaged either way. They travel INTO the
    consolidated shard so the analysis agent can apply its own standard, and they never
    gate admission here.

  ── The part that is easy to get wrong ────────────────────────────────────────────

  "Buggy" is **not a property of a campaign**. It is a property of individual
  channel-days, and it totals 209,867 of 5,608,243 — **3.74%** across both campaigns.
  Treating v1 as the buggy campaign and discarding it would throw away 2.36M clean
  channel-days to avoid 67k dirty ones.

  **Most of the dirty days are repairable with no provider contact** (R-19). Sampling 40
  affected stations per campaign and parsing each shard's own embedded
  `_stationxml_raw`: ~77% of non-metre channel-days have a usable response sitting
  inside the very file that was written in counts. They are fixable offline.

  So the cleanup is **repair-then-admit, not delete**:

    repairable        -> P4b fixes them offline; they enter the product as clean days.
    unrepairable      -> response genuinely absent from the embedded XML. This is
                         R-12's population; excluded from the product, recorded with
                         the reason, re-fetched later.

  ── P4b: END-TO-END repair (PI decision, 2026-10-08) ──────────────────────────────

  **Decision: repair R-19 end-to-end — re-download and re-package — not by offline
  deconvolution of the already-packaged counts data.**

  The reasoning is the operation-order caveat. The packaged counts data has ALREADY been
  decimated to 1 Hz and filtered. Deconvolving afterwards is not order-identical to
  deconvolving before: the operations are linear and commute in principle, but
  decimation's anti-alias stage and the water-level stabilisation are where that breaks
  down. For the NCF band (10-40 s) it sits far inside Nyquist and below the 0.4 Hz
  lowpass, so it probably would have been fine — but "probably fine" is not a property
  you want underneath an archive that other people will treat as correct.

  **What "end-to-end" costs, measured — and it is mostly NOT a download.** The decision
  is about the PROCESSING CHAIN, not about the bytes being stale. Re-packaging from raw
  SEED we already hold runs the identical correct chain (deconvolve -> decimate ->
  filter), so it satisfies the decision in full without touching a provider.

    campaign   affected sta   bad days    where the raw SEED is
    V2                   70    152,924    60 sta / 144,667 d  STILL ON SCRATCH
                                           9 sta /   8,257 d  absent
    V1                   51     67,179    29 sta /  38,596 d  terravibranium archive
                                          22 sta /  28,583 d  absent
    union                93                (28 stations affected in BOTH)

  **~89% of affected channel-days have their raw SEED already in hand.** Three tiers,
  cheapest first:

    1. REPACKAGE-SCRATCH   60 v2 stations. Raw is in `scratch_work/`. This is STEP 4's
                           existing, already-tested `WAVENET_SKIP_DOWNLOAD=1` path --
                           no new code and no network for the largest tier.
    2. REPACKAGE-TERRA     29 v1 stations, 359,813 files in
                           `/RAID6/lab_archive/wavenet_ncf_raw_seed/v1`. Pull back by
                           tar-stream (the archive move in reverse), then tier 1.
    3. RE-DOWNLOAD         stations whose raw is in NEITHER place. The v1-absent (22)
                           and v2-absent (9) sets overlap heavily, so this is roughly
                           two dozen stations -- small enough to run inside the
                           compliant 5-connection cap without disturbing R-1.

  **The true repair set is smaller than 93**, and the merge shrinks it for free: a
  channel-day that is dirty in v1 but clean in v2 needs no repair at all, because
  consolidation already selects the clean copy. Only channel-days dirty in EVERY
  campaign that holds them are real repair targets. Compute that residual BEFORE
  queueing any download — 28 stations are affected in both campaigns and are the only
  ones where cross-campaign selection cannot help.

  ── Validation: ground truth exists, 45 channels ──────────────────────────────────

  Across the 1,006 stations present in both campaigns there are 45 station-channels
  packaged as COUNTS in one campaign and as properly-deconvolved METRES in the other,
  with overlapping days:

    KZ.MAKZ  BHE   v1 counts 1,229 d  |  v2 m 3,364 d
    KZ.MAKZ  BHN   v1 counts   670 d  |  v2 m 2,627 d
    X5.CTSN  BHE   v1 counts   179 d  |  v2 m   769 d
    S1.AUCAR BHZ   v1 m        307 d  |  v2 counts 1,963 d   (it runs both ways)

  These remain the validation gate even though the repair is now end-to-end: re-package
  the counts copy and compare against the metres copy on the shared days. It is a
  known-good answer to check against rather than a plausibility argument (§14), and it
  also independently confirms that the repackage path reproduces the correct chain.

  The comparator must VOID on an empty comparison — a bit-exactness checker on this
  project once reported BIT-IDENTICAL having compared zero channel-days.

  Deleting non-metre days outright would destroy ~133k recoverable channel-days to
  reclaim roughly 60 GB against 42 TB of free archive space. That trade is strictly
  bad (§5 — never discard data you can correct).

  The merge remains **qualification-gated, not union-by-default**: a day is admitted
  only if it passes an explicit predicate (units, sampling rate, response, patch level),
  evaluated per channel-day AFTER the repair pass.

  **A day that fails is NOT silently dropped.** It is recorded in the master index as
  present in source X, excluded, with the reason — §1, silence is not success. The index
  must answer "what do we hold that we chose not to use, and why": that is the input to
  a later repair and the only honest basis for a completeness number.

  ── Artifact 1: the consolidated shard (the product) ──────────────────────────────

  One HDF5 per station, `<NET>.<STA>.h5`. **All metadata lives inside the file** (PI):
  the shard must be independently interpretable with no sidecar and no index.

    station attrs   network, station, latitude, longitude, elevation
    provenance      consolidation code commit + version, build timestamp, and the
                    source campaign + producing commit for each contributing shard
                    -- this closes R-8, which is why several issues were hard to attribute
    schema          schema_version, patch_level, day-grid epoch (1970-01-01), units
    per channel     units, sampling_rate, response metadata, _stationxml_raw
    per channel-day _coverage bitmap, QC flags, and SOURCE (which campaign gave this day)
    exclusions      days held in some source but not admitted, with reason codes

  Written per station as `.tmp` then `os.replace` — atomic, so a killed job leaves no
  half-shard. Never written in place over a source.

  ── Artifact 2: the master index (the redundancy) ─────────────────────────────────

  Parquet, rebuildable from the shards at any time — it is a crawling convenience, not
  a source of truth, and nothing may depend on it that cannot be recomputed.

    stations.parquet      one row per station: lat/lon, date range, total days, bands,
                          component slots, contributing sources, bytes, checksum
    station_days.parquet  one row per station-channel-day: source, admitted/excluded,
                          reason code, QC flags

  This is also the `MANIFEST.md` the 2026-09-30 archive promised and never had.

  ── Verification (P7), and what makes it real ─────────────────────────────────────

  The check compares the consolidated shard against the archived originals on
  **day-sets and sample data**, not on counts. Counts agreeing is not evidence; two
  wrong numbers agree all the time, and a bit-exactness checker on this project once
  reported BIT-IDENTICAL having compared zero channel-days.

  So the comparator must **VOID on an empty comparison** rather than pass, and must be
  validated against a known-good and a known-bad case before any failing result from it
  is believed (§14).

  Gate to proceed: every admitted day in the product is byte-identical to its source
  day, and every source day is either admitted or carries an exclusion reason. No
  silent difference in either direction.

  ── Where each phase runs ─────────────────────────────────────────────────────────

  Consolidate where the data already is; moving 1.7 TB twice is the thing to avoid.

    v1 + v2 merge    BlueHive3 (both on the same scratch filesystem)
    SAmer side       terravibranium (its own RAID6; now idle at load 6.6)
    final join       whichever side ships second, at the destination
    master index     axon-1 or cerebrum, from the shipped shards -- cheap, metadata only
    archive          atos /volume1

HARDWARE TIER LOG

  | Tier            | Status | Date | Job ID | Log link |
  |------------------|--------|------|--------|----------|
  | axon-1 (local)    | atos access verified | 2026-10-08 | n/a | — |
  | terravibranium      | not started |  |  |  |
  | Bluehive3            | atos reachability verified | 2026-10-08 | 2091798 | — |
  | atos                  | write/checksum/delete verified | 2026-10-08 | n/a | — |

DECISION

  Accepted as canonical going forward:
  * **The archive carries only clean, usable data** (PI). Buggy originals are not
    archived.
  * **Retain is not archive.** Originals stay in place until P7 verifies; nothing is
    deleted before that. Scratch has 553 TB free, so retention costs nothing.
  * **Repair before exclusion.** ~77% of non-metre channel-days are fixable offline from
    responses already embedded in the shards (R-19). Repair them; do not delete them.
  * One consolidated shard per station, fully self-describing (PI).
  * Master index is redundant and rebuildable, never authoritative (PI).
  * Merge at channel-day granularity, qualification-gated, exclusions recorded with
    reasons rather than dropped.
  * Precedence is **quality-first**, campaign order only as a tie-break — v1 measured
    cleaner than v2, so a blanket v2>v1 rule would prefer the dirtier copy.
    FPS > SAmer on the 101 overlaps, after the P4 consistency check.
  * Destination atos `/volume1` (PI). The 2026-09-30 archive is retired **after P7
    passes**, not at upload time, with a note recording why (PI).
  * Consolidation happens AFTER the single issue-fix deploy and completeness campaign.

  ANSWERED 2026-10-08 (PI)

  1. "Clean" means **units only**. Sampling-rate jitter is accepted and corrected in
     post-processing. QC flags are advisory metadata and travel with the data.
  2. The 788 non-manifest SAmer stations go in the **same directory**, with **no
     overlaps** against the original manifest — the 101 shared stations come from FPS.
  3. The R-19 repair is **not** blocked by the raw-SEED migration: it needs no raw SEED
     and no provider, only the shard's own embedded response.

OPEN QUESTIONS FOR PI

  1. **Scope of the R-19 offline repair**: run it across everything before consolidation
     (cleanest product on the first pass, more work up front), or admit repairable days
     flagged and repair in a later pass (faster to a usable archive)? Recommend the
     former — the repair needs no provider, so it will never get cheaper than now.
  2. **If P4b's 45-channel validation FAILS** — i.e. deconvolving post-decimation does
     not reproduce the correctly-processed copy within tolerance in the target band —
     the ~133k repairable days become re-download work instead, and that DOES depend on
     raw SEED and provider rate limits. Worth deciding in advance whether that outcome
     means "re-download them" or "exclude them and record it", because it changes the
     completeness campaign's scope.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
