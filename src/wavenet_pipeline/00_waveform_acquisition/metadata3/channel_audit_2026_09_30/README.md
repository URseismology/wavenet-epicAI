# Channel audit and station replacement — 2026-09-30

**Which pipeline this belongs to:** the **BlueHive3** global download campaign
(`production/`, 2,000-station farthest-point-sampled network). It has nothing to do with
terravibranium, which packages the separate SAmericaNoise ROVER archive and performs no
downloads. The analysis was *run* on terravibranium only because that machine had a free
shell and these are FDSN metadata lookups; the subject is BH3 throughout.

## Why

Of the 2,000 locked stations, **954 returned no data**. This audits every one of them
against FDSN, explains why, and — for the subset that has no seismometer at all — finds
replacement stations.

## Result

| Count | Category | Disposition |
|---:|---|---|
| 433 | `has_BH_LH_should_have_worked` | Config: `location_priorities` excludes `01`/`02`/`30`/`31`/`32` |
| 312 | `missed_other_seismometer_band` | Config: widen channel request beyond `BH?,LH?` |
| 208 | `no_seismometer_on_station` | 108 replaced, 1 deleted, 99 outstanding |
| 1 | `manifest_defect` | `nan.SABA` — empty network code |
| 0 | `no_fdsn_match_iris` | — |
| 0 | `exists_no_channels` | — |

Every station matched FDSN and had channels registered, so nothing was lost to bad
identifiers or empty inventories.

**Second manifest defect found:** `XB.ELYH0` (rank 693) is the InSight lander at
4.502N 135.623E — **on Mars**. Deleted rather than replaced.

## Selection rules applied

A replacement must be a real seismometer — SEED instrument code (2nd char) `H`, high gain.
That excludes accelerometers (`?N?`), low-gain seismometers (`?L?`), and the state-of-health
families (`VM` mass position, `LOG`, `ACE`, `Q*`) that a naive "lowest sample rate" rule
would otherwise select: `VM0`-`VM6` offers seven components at 0.1 Hz and would beat LH.

Sampling rate must be **>= 1 Hz**. The floor is Nyquist: the product is 1 Hz and the
pipeline lowpasses at 0.4 Hz, so 1 Hz sampling (Nyquist 0.5 Hz) is the minimum that
preserves the target band. `VH?` is a genuine high-gain seismometer at 0.1 Hz and is
excluded for exactly this reason.

Ranking is **distance first** (nearest triad wins), with lowest sampling rate deciding which
band to take *at* a station — so MH over SH. Acceptance threshold: **0.5 degrees**.

Data availability is a **hard gate**: metadata registration is not availability, and
conflating the two is what produced category 1 in the first place.

## A correction worth knowing

The first availability probe sampled 5 fixed windows inside the original station's operating
years and had a **39% false-negative rate** — 9 of 23 rejected candidates did have
contemporaneous data. `reprobe_all.py` reads each candidate's real channel epochs, intersects
them with the original's window, and probes 12 windows inside that intersection. Six
recoveries came back within 0.5 deg. Do not reuse the original fixed-window approach.

## Files

| File | What it is |
|---|---|
| `channel_audit_results.csv` | All 954, with lat/lon, years, category, channels found |
| `replacement_candidates.csv` | All 208 searched, with top candidates and distances |
| `replacements_accepted.csv` | The 108 adopted (<= 0.5 deg, high-gain, data confirmed) |
| `replacements_rejected.csv` | Rejected, each with its reason |
| `reprobe_results.json` | Epoch-aware re-probe of the "no data" cases |
| `manifest_v2_changes.csv` | Every change applied to build `fps_stations_v2.csv` |

| Script | What it does |
|---|---|
| `find_replacements.py` | Radius search + band filter + availability gate |
| `reprobe_all.py` | Epoch-aware availability re-check |
| `merge_reprobe.py` | Folds recoveries into the accepted set |
| `finalize_replacements.py` | Applies Mars removal, low-gain drop, 0.5 deg threshold |
| `check_redundancy.py` | Guards against replacements duplicating existing coverage |
| `campaign_rollup.py` | Campaign-level disposition |
| `build_manifest_v2.py` | Writes `../fps_stations_v2.csv` |
| `prove_recovery.py` | Tests whether the proposed config actually retrieves data |

> `prove_recovery.py` must run on **BH3**, not terravibranium. On a machine saturated with
> packaging workers, `MassDownloader` cannot even construct itself
> (`RuntimeError: can't start new thread`) and every station silently reports zero bytes —
> which looks exactly like a definitive "nothing is recoverable" result.

## Output

`../fps_stations_v2.csv` — 1,999 stations: 108 replaced, `XB.ELYH0` deleted, `rank`
preserved so the farthest-point ordering is unchanged. `days` is cleared on swapped rows
because the replacement's true day count is unmeasured. The original `fps_stations.csv` is
untouched; adopting v2 is a separate, deliberate step.
