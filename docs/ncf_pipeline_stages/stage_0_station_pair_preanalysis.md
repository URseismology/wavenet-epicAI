━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE 0 — STATION & PAIR PRE-ANALYSIS (SPATIAL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  A farthest-point-sampled subset of EarthScope's ~50,000-station inventory can give
  globally even spatial coverage without the clustering bias of using all stations or a
  naive random subset, and a distance+shared-recording-days filter on top of that subset
  yields a cross-correlation-valid pair network.

SETUP
  Branch: `add-september-ncf-pipeline` (commit `fc1b265`, author `localxz`).
  Code: `src/wavenet_pipeline/00_waveform_acquisition/metadata3/`
  (`s3_inventory.ipynb`, `fps_downsample.py`, `build_pairs.py`, `build_key_index.py`,
  `day_index.py`, `plot_metadata_panel.py`, `plot_connection_heatmap.py`).

WHAT WAS TRIED
  1. `s3_inventory.ipynb` (Colab) scanned the full EarthScope S3 bucket, counting real
     per-station-day miniseed objects (not FDSN's nominal operational dates) to produce
     `s3_inventory.csv` (~50,000 candidate stations, `days` = empirical recording-day
     count within each station's FDSN-reported operational window).
  2. `fps_downsample.py` selected 2,000 stations via greedy farthest-point sampling on
     the sphere, seeded from the highest-`days` station, after a `>=15 day` minimum
     filter. Deterministic and nested (a bigger run's first N rows match a smaller run).
  3. `build_pairs.py` kept only pairs with distance 110-1,110 km AND shared recording
     days >= 90 (under 1,500 km) / >= 365 (over — currently dormant, since the band
     tops out at 1,110 km).
  4. `plot_metadata_panel.py` / `plot_connection_heatmap.py` rendered the spatial result.

RESULTS
  - 2,000 stations selected (`fps_stations.csv`), evenly spread globally.
  - 40,170 pairs geometrically legal within the 110-1,110 km band; 9,743 survived the
    shared-days gate (24.3% pass rate) -> `fps_pairs.csv`.
  - `metadata_panel.png` (station map), `connection_heatmap.png` (2°-grid pair-density
    heatmap) confirm no over-representation of already-dense regions (e.g. North
    America/Europe aren't favored just because more stations exist there).

HARDWARE TIER LOG
  | Tier            | Status | Date       | Job ID | Log link |
  |------------------|--------|------------|--------|----------|
  | axon-1 (local)    | done  | 2026-09-14 |  n/a   | commit fc1b265 |
  | mothership         | n/a  |            |        | |
  | terravibranium      | n/a  |            |        | |
  | Bluehive             | n/a  |            |        | |

DECISION
  Accepted as the canonical station set and pairing logic for all downstream stages.
  **The 2,000-station selection is fixed and must not be regenerated or changed by any
  later stage** (explicit PI instruction, 2026-09-23) — only the distance/duration
  parameters in `build_pairs.py` may be relaxed going forward (see Stage 1).

OPEN QUESTIONS FOR PI
  None outstanding for this stage — retroactively documented as part of formalizing
  pipeline tracking (Stage 1 work, 2026-09-23).

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
