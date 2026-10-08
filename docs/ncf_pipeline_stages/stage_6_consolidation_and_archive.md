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

  ── Ordering principle ────────────────────────────────────────────────────────────
  **Archive the originals BEFORE consolidating, not after.**

  Consolidation is new code performing a merge across every byte we hold, and merges
  are the single defect class that has most often destroyed data in this pipeline. If
  the consolidated product is what gets archived, a merge bug has no recoverable ground
  truth — and the merge is the step most likely to carry the bug.

  Storage makes this free: 1.7 TB of originals is 4% of atos's free space. Archiving
  first makes consolidation REVERSIBLE, which is the only durable way to satisfy "do it
  in a way that does not introduce bugs" (PI, 2026-10-08). Principles §5 (never package
  data you cannot correct) and §6 (keep raw until the product is verified).

  ── Phase ordering, with the issue work interleaved ───────────────────────────────

    P0  Archive originals, unmodified, to atos.          <- CAN START NOW for v1 + SAmer
    P1  Issue fixes: isolated test per issue.            <- see CAMPAIGN_ISSUES.md
    P2  ONE integration test of the combined fix set.
    P3  ONE deploy + completeness campaign.
    P4  Consistency check on the 101 overlap stations.
    P5  Consolidation (test -> verify -> deploy).
    P6  Master index.
    P7  Verify consolidated against the archived originals.
    P8  Archive the consolidated product; rebuild JSONs from it.
    P9  Hand to the delivered-network analysis agent (Stage 5 re-run).

  P0 splits by quiescence: **v1 and SAmer are static and can go now; v2 is being
  written by STEP 4 and R-1 right now**, so archiving it today captures a torn state.
  v2 goes after R-1 lands.

  Consolidation is deliberately AFTER the completeness campaign (P3). Consolidating
  first would mean doing it twice, and the second pass would be the one that counts.

  ── The merge rule ────────────────────────────────────────────────────────────────

  Unit of merge is the **channel-day**, not the station and not the file. The absolute
  1970-anchored day grid already shared by the v2 schema makes this an index operation
  rather than a time alignment.

  Precedence, per channel-day present in more than one source:

    1. **v2 over v1.** v2 carries the response fix (R-3), the channel audit, and
       `patch_level` 3. v1 predates them.
    2. **FPS (v1/v2) over SAmer** for the 101 overlapping stations — PI, 2026-10-08 —
       **after** the P4 consistency check, not before it.
    3. A source contributes a day only if NOTHING else holds it.

  ── The part that is easy to get wrong ────────────────────────────────────────────

  **v1 days are not automatically safe to merge.** v1 predates the response fix, so an
  unknown number of its channel-days are in COUNTS rather than metres. The v2 census
  already shows 169 channels still in counts and 16 in derived displacement; v1 is
  expected to be worse. A naive union would import those days into the consolidated
  product and quietly poison a dataset whose whole purpose is cross-correlation.

  So the merge is **qualification-gated, not union-by-default**. A day is admitted only
  if it passes an explicit predicate — canonical units, sampling rate, `response_ok`,
  `patch_level` — evaluated per channel-day.

  **A day that fails is NOT dropped.** It is recorded in the master index as present in
  source X, excluded, with the reason. Silently omitting it would reproduce exactly the
  failure §1 exists to prevent: silence is not success. The index must be able to answer
  "what do we hold that we chose not to use, and why" — that is the input to a later
  repair, and the only honest basis for a completeness number.

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
  * Archive originals first; consolidate second; verify against the archive third.
  * One consolidated shard per station, fully self-describing (PI).
  * Master index is redundant and rebuildable, never authoritative (PI).
  * Merge at channel-day granularity, qualification-gated, with exclusions recorded
    rather than dropped.
  * Precedence v2 > v1; FPS > SAmer on the 101 overlaps, after a consistency check.
  * Destination atos `/volume1` (PI).
  * Consolidation happens AFTER the single issue-fix deploy and completeness campaign.

OPEN QUESTIONS FOR PI

  1. The 2026-09-30 atos archive (1,013 shards, 451 GB) is superseded but not wrong.
     Keep as a dated snapshot, or retire once the new archive verifies? Retiring frees
     451 GB we do not currently need — recommend keeping it until P7 passes.
  2. Qualification predicate: should a day in COUNTS be excluded outright, or admitted
     with a units flag so a later pass can correct it in place? Excluding is safer;
     admitting-with-flag preserves more and defers the decision to the analysis agent.
  3. Does the consolidated product carry the 788 non-manifest SAmer stations in the
     same directory as the manifest network, or in a parallel one? Stage 5 reports them
     as two populations either way; this is about on-disk layout for the analysis agent.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
