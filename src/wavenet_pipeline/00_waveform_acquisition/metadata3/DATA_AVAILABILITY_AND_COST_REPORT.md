# Data Availability & Cost Report — Fixed 2,000-Station NCF Network

*Built 2026-09-23 from the full EarthScope key index
(`keys_partitioned_year_full/` — 513 networks, 60,125 stations, 35M records, 1.09 PB
total archive, ~3h to build via `build_key_index.py`; see its own README). Queried down
to our fixed, locked 2,000-station network (`fps_stations.csv`) via
`build_key_index_summary.py`, `build_connectivity_analysis.py`, and
`build_coverage_density_analysis.py`.*

---

## TL;DR

| Question | Answer |
|---|---|
| Full history, all 2,000 stations, deduplicated | **78.9 TB raw** |
| ...packaged (processed + compressed) | **~5.0 TB** |
| Connectivity-filtered download (current band) | **68.6 TB raw** — barely less than full history |
| Marginal cost of widening the band | **Small** (~3-7 TB across all bands tested) — cost is not the binding constraint |
| Stations with zero valid connections today | **367 / 2,000 (18.4%)** — the most actionable finding here |
| What actually should drive the band choice | **Ray-path coverage density**, not cost or raw pair count (§5) |
| AWS **EC2 processing** cost (full dataset, dominates the total) | **~$1,635** (illustrative — instance-pricing assumption, not re-verified against current AWS pricing) |
| AWS **egress** cost (packaged output only, ~5 TB) | **~$447** (real formula, $0.09/GB — this part is solid) |
| AWS total (process + egress + trivial GET requests) | **~$2,083** |
| Does more parallelization cost more? | **No — same total EC2 dollar cost at any parallelization level (§6), just faster wall-clock.** Standard cloud-elasticity: cost = hours × rate, and the two cancel as you add workers. |
| Bluehive to do the same | **~$0** marginal (already-allocated compute), similar wall-clock |
| Decision made this session | ObsPy `MassDownloader` + Bluehive `urseismo`, **not** AWS or ROVER (§7) |
| **Deployment strategy** (PROGRESS.md) | Not either/or — (1) build and prove out extensively on Bluehive, package as Docker, (2) test AWS on a few stations, (3) deploy on both per this report's cost/time numbers. AWS's case isn't cost, it's **reach** (portable, sharable code) funded by cloud research grants, not lab budget — Bluehive stays the free science workhorse either way. |
| Decision **not yet** made | Which distance/duration band to commit to (§5), whether to backfill uncovered stations (§9) |

## Contents

1. [The full EarthScope archive, for scale](#1-the-full-earthscope-archive-for-scale)
2. [Our fixed 2,000-station network](#2-our-fixed-2000-station-network)
3. [Pair connectivity at current & relaxed bands](#3-pair-connectivity-at-current--relaxed-bands)
4. [North Star: minimum-coverage sizing](#4-north-star-minimum-coverage-sizing)
5. [Ray-path coverage density — the real design objective](#5-ray-path-coverage-density--the-real-design-objective)
6. [Full-network processing + egress cost on AWS](#6-full-network-processing--egress-cost-on-aws)
7. [Acquisition path comparison: AWS vs. ObsPy vs. ROVER](#7-acquisition-path-comparison-aws-vs-obspy-vs-rover)
8. [Existing local archives — don't redownload](#8-existing-local-archives--dont-redownload)
9. [Open items / next steps](#9-open-items--next-steps)
10. [Files in this report](#10-files-in-this-report)

---

## 1. The full EarthScope archive, for scale

| Metric | Value |
|---|---|
| Networks scanned | 513 |
| Unique stations | 60,125 |
| Total records | 35,026,531 |
| **Total raw data** | **1,091,043.9 GB (~1.09 PB)** |
| Year range | 1969–2046 *(2046 is almost certainly a source-archive metadata anomaly — flagged, not investigated further)* |
| Scan cost | ~$0.50 (LIST requests only — no egress at all) |
| Data-quality note | Network `UW` hit a token-expiry retry mid-scan; its count (3,277,000 keys) is not fully confirmed clean |

---

## 2. Our fixed 2,000-station network

- 321 distinct networks represented.
- **2,770,643 records** (station-days) — filtered from the full 35M via network + exact-station match.
- **1,984 / 2,000 stations have real data**; 16 show zero (worth checking whether that's a real gap or a code mismatch — not yet investigated).
- **78.9 TB raw, full history, deduplicated** — the number that matters for "download everything, ever." Not to be confused with the pair-level figures in §3, which are a *subset* of this by construction.
- Per-station days: median 496.5, mean 1,385, max 15,050 (~41 years) — highly skewed by design (FPS seeding favored long-running stations first).

See `key_index_summary/daily_coverage_timeline.png` for the visual "map of connected days."

---

## 3. Pair connectivity at current & relaxed bands

**Correct mental model (PI, 2026-09-23)**: we download, process, and package each
station's data **once**. Connectivity's job is to identify which days are *not* worth
downloading at all (no valid partner that day) — so the connectivity-filtered volume
must always be **≤** the 78.9 TB ceiling, never more.

| Band (km) | Geometric pairs | Pass shared-days gate | Pass rate | Stations connected | Required download |
|---|---|---|---|---|---|
| 110–1,110 (current) | 40,170 | 10,626 | 26.5% | 1,633 / 2,000 | **68.6 TB** |
| 110–1,500 | 64,539 | 15,508 | 24.0% | 1,672 / 2,000 | 72.0 TB |
| 110–2,000 | 99,700 | 19,453 | 19.5% | 1,680 / 2,000 | 73.3 TB |
| 110–3,000 | 177,751 | 28,385 | 16.0% | 1,685 / 2,000 | 74.8 TB |
| 50–5,000 | 361,360 | 52,042 | 14.4% | 1,692 / 2,000 | 75.4 TB |

All below the 78.9 TB ceiling, as they must be. Widening the band recovers a few more
connected stations (1,633 → 1,692) and closes in on the ceiling, but never exceeds it —
**and the marginal cost of doing so is small** (a few TB, not tens).

> **A real bug was caught and fixed here, not just cleaned up presentation-wise**: the
> first version of this table summed each *pair's* overlap-bytes independently, which
> double-counts any station in multiple pairs (average degree ~10.6/station) — that
> version showed 598.6 TB, *larger* than the 78.9 TB ceiling, which is logically
> impossible. Fixed by taking the union of required days **per station** before summing.

> **Separate correction**: the already-committed `fps_pairs.csv` (built against an
> earlier, incomplete key-index scan) reports 9,743 pairs at the current band; this
> report's complete index shows **10,626** — a ~9% increase from better coverage.
> `fps_pairs.csv` itself is **not** regenerated (station/pair selection stays locked).

---

## 4. North Star: minimum-coverage sizing

The smallest set of pairs such that every station touches at least one, sized at
packaged (not raw) volume.

| Metric | Value |
|---|---|
| Stations covered | 1,633 / 2,000 |
| **Stations with NO valid pair at all** | **367 (18.4%)** — the most actionable finding in this report |
| Pairs selected (greedy minimum-edge-cover) | 1,486 |
| Unique station-days required | 2,241,961 |
| Required download (deduplicated) | **67.1 TB** — close to §3's "all valid pairs" 68.6 TB; North Star only prunes the genuinely redundant extra pairs |
| Estimated packaged volume | **~4.02 TB** |

The 367-station gap means over a sixth of the fixed network currently can't
participate in any cross-correlation pair at all, under the current 110–1,110 km /
90–365-day thresholds. A wider band recovers some of them (§3), at the coverage/
redundancy tradeoff analyzed in §5. **Not yet resolved** — open decision for the PI.

*(These numbers use the freshly-recomputed, complete 10,626-pair set — not the stale
`fps_pairs.csv`. Earlier figures reported mid-session, 407/1,593, are superseded.)*

---

## 5. Ray-path coverage density — the real design objective

Per PI direction: the goal is not to maximize pair *count* — it's global ray-path
coverage **density and uniformity** with limited redundancy. Computed on the same 2°
grid `plot_connection_heatmap.py` uses.

| Band | Pairs | Coverage | Redundancy (CV) | Marginal efficiency (new cells / 1,000 pairs) |
|---|---|---|---|---|
| 110–1,110 (current) | 10,626 | 31.9% | 1.189 | **486.0** |
| 110–1,500 | 15,508 | 39.7% | 1.294 | 259.1 |
| 110–2,000 | 19,453 | 45.6% | 1.351 | 244.9 |
| 110–3,000 | 28,385 | 61.9% | 1.501 | 293.9 |
| 50–5,000 | 52,042 | 82.7% | 1.499 | 142.9 |
| 50–10,000 | 142,888 | 90.5% | 1.244 | 13.8 |
| 50–20,015 (180°, max possible) | 254,862 | 90.7% | 1.028 | **0.34** |

**The "optimal band" pattern shows up once the full 180° range is tested** — marginal
efficiency collapses (486 → 143 → 13.8 → 0.34 new cells/1,000 pairs) past roughly
5,000–10,000 km. The redundancy CV *dropping* at the widest bands (1.50 → 1.03) is a
**saturation artifact**, not real efficiency — once ~90% of cells are covered, more
paths just re-trace already-covered ground at higher density, mechanically lowering
variance. **Marginal efficiency, not CV, is the metric that reveals the real sweet
spot**, and it points toward the low-thousands-of-km range, not the widest band tested.

> **Caveat this analysis doesn't capture**: map coverage says nothing about whether
> long paths are *useful* for the project's target period band (1–20s, per the ML
> pipeline's FTAN grid) — wavelength/depth sensitivity means very long paths may not
> usefully constrain short-period dispersion regardless of map coverage. A domain
> judgment, not decided by this metadata-only analysis.

---

## 6. Full-network processing + egress cost on AWS

For the full 78.9 TB raw dataset (all 2,000 stations, entire history), using confirmed
real per-channel-day figures throughout:

| Metric | Value |
|---|---|
| Total channel-days (5 channels/station × 2,770,643 station-days) | 13,853,215 |
| **Packaged size** | **~4.97 TB** (~15.9x reduction from raw) |
| Serial processing time (10 s/channel-day, middle of confirmed 5-16s range) | ~4.4 years |

**Parallelization strategies** (same total compute, different instance sizing —
illustrative `c5`-family on-demand pricing, not re-verified against current AWS rates):

| Workers | Wall-clock | Illustrative instance | Total EC2 cost |
|---|---|---|---|
| 8 | ~200 days | `c5.2xlarge` | ~$1,635 |
| 16 | ~100 days | `c5.4xlarge` | ~$1,635 |
| 32 | ~50 days | `c5.9xlarge` | ~$1,840 |
| 64 | ~25 days | `c5.18xlarge` | ~$1,840 |
| **96** | **~16.7 days** | `c5.24xlarge` | **~$1,635** |
| 120 (matches `urseismo`'s real core count) | ~13.4 days | — | ~$1,635 |
| 384 (4x `c5.24xlarge`) | ~4.2 days | 4x `c5.24xlarge` | ~$1,635 |

**Real insight, not just a table**: total dollar cost stays roughly flat (~$1,635-1,840)
*regardless of parallelization level* — standard cloud-elasticity property (cost =
hours x rate; rate scales ~linearly with vCPU count while hours scales inversely, so
they cancel). More parallelism buys **speed**, not savings or penalty, in this
linear-pricing regime. The minor variance between rows is imprecision in the
illustrative per-instance-type rate assumptions, not a real cost/parallelism tradeoff.

**AWS dollar cost, zero-egress architecture** (process in `us-east-2`, egress only the
packaged output):

| Component | Estimate | Basis |
|---|---|---|
| S3 GET requests | ~$1 | negligible at any scale |
| EC2 compute (~16.7 days, 96-way parallel) | ~$1,635 | **illustrative** — ~96-vCPU instance @ ~$4/hr, not re-verified against current AWS pricing |
| **Egress of the final packaged H5 only** | **~$447** | the real number that matters — raw data never leaves `us-east-2` |
| **Total (illustrative)** | **~$2,083** | |

**Bluehive comparison**: ~$0 marginal cost (already-allocated academic compute),
similar wall-clock. The real AWS cost is the **EC2 compute time**, not egress — the
zero-egress architecture already keeps egress ~16x cheaper than egressing raw data
would be (~$447 vs. ~$7,100).

---

## 7. Acquisition path comparison: AWS vs. ObsPy vs. ROVER

| | AWS zero-egress | ObsPy `MassDownloader` (chosen) | ROVER (retired) |
|---|---|---|---|
| Dollar cost | Small but real (LIST/GET/egress/EC2) | **$0** — public FDSN, no AWS at all | $0, but needs a workaround |
| Works today, no fix | Yes | **Yes** | No — depends on a retired FDSN service |
| Gets StationXML free | No | **Yes** | No |
| Confirmed preprocessing cost | n/a | **5-6 s/channel-day** (`LH`) to **16 s** (`BH`-only) | same, with the workaround |
| Confirmed storage cost | n/a | **300-410 KB/channel-day** | same |
| Confirmed scaling | n/a | **Linear** to 20-way concurrency | untested (retired first) |
| Infra maturity here | Registry + container patterns exist | Proven this session end-to-end | Extensive, but all needs the same fix |

**Decision already made**: ObsPy `MassDownloader` + Bluehive `urseismo`. This table is
for completeness, not an open choice.

---

## 8. Existing local archives — don't redownload

Per `EXISTING_DATA_INVENTORY.md`: real archives already exist on `atos`/Bluehive
scratch (`GT-BOSA` 132 GB, `GT-DBIC` 116 GB, `PrjXX_SAmericaNoise` 11 TB). **Whether
these overlap with our 2,000-station network is not yet checked** — if a meaningful
fraction does, effective new-download volume could be well below the §2/§4 figures.

---

## 9. Open items / next steps

Explicitly not done, not silently assumed away:

- **Overlap check** between existing local archives (§8) and our fixed network.
- **Replacement-station search** for the 367 uncovered stations — find the nearest
  real alternative (bounded FDSN query) that has confirmed archived data, without
  touching the locked FPS station selection itself. In progress (see
  `find_replacement_stations.py`) — checks *any* data presence only; channel-level
  (`BH?`/`LH?`) confirmation deferred to a later per-candidate check.
- **Final band/duration decision** — this report gives the metadata; §5's coverage-
  density tradeoff plus the domain caveat there is what should actually drive it.
- **AWS EC2 pricing** in §6 needs live re-verification before being used for a real
  budget decision.

---

## 10. Files in this report

| File | What it is |
|---|---|
| `key_index_summary/station_summary.csv` | Per-station days/bytes/date-range |
| `key_index_summary/station_day_index.parquet` | The real per-station-day list (2.77M rows, long format) |
| `key_index_summary/station_connectivity.csv` | Per-station: connection count + partner list |
| `key_index_summary/daily_coverage.csv` / `daily_coverage_timeline.png` | Map of connected days |
| `key_index_summary/pair_size_estimate.csv` | §3's table, machine-readable |
| `key_index_summary/north_star_min_coverage.csv` / `north_star_summary.csv` | §4's selected pairs + stats |
| `key_index_summary/connection_heatmap_improved.png` | §3/§5's spatial heatmap (complete pair set) |
| `key_index_summary/range_bucket_breakdown.csv` | Short/medium/long-range pair breakdown |
| `key_index_summary/coverage_density_by_band.csv` | §5's full data |
| `key_index_summary/replacement_station_candidates.csv` | §9's in-progress replacement search |
