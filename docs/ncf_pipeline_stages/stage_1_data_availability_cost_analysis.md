━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE 1 — DATA AVAILABILITY, SIZE & COST ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  Before downloading anything, we can determine (a) real per-station-day data size
  (not just day-counts), (b) actual connectivity/reachability of both the AWS/EarthScope
  zero-egress path and the plain-FDSN/ROVER path, and (c) a defensible cost estimate for
  each acquisition strategy — all without moving any waveform data yet, and without
  changing the Stage 0 station selection.

SETUP
  Branch: `add-september-ncf-pipeline`, working locally on axon-1
  (`/Users/urseismoadmin/wavenet-epicAI`).
  New machine added to the project: **mothership** (10.17.7.237, user `olugboji`) — has
  a real, live-verified AWS IAM identity (`atos-orchestrator`) and EarthScope login
  (`tolulope.olugboji@rochester.edu`), added as an SSH alias on axon-1
  (`~/.ssh/config`), passwordless via `ssh-copy-id`.
  Reference docs: `chrisScripts/AWS_Setup_Guide.md`, `chrisScripts/AWS_Docker_Pipeline_Guide.md`,
  `OlugbojiLab-Wiki/AWS_EarthScope_Access_Guide.md`.

WHAT WAS TRIED
  1. **AWS connectivity — confirmed live.** `boto3.client('sts').get_caller_identity()`
     on mothership returned `arn:aws:iam::581009776195:user/atos-orchestrator`.
  2. **EarthScope connectivity — confirmed live.** `es user get-profile` (EarthScope CLI,
     installed via `pip install --user earthscope-cli earthscope-sdk`, run interactively
     by the PI since `es login` requires a human) returned a valid, non-secret profile
     (name/institution/email). One attempt to inspect `get-aws-credentials` output was
     stopped mid-flight by Claude Code's own credential-materialization safety
     classifier before any secret was exposed — noted here so it isn't repeated.
  3. **Size capture added to `build_key_index.py`.** The existing script already calls
     S3 `list_objects_v2` per network but discarded `obj['Size']`. Added a `size_bytes`
     column (int64) — this is the *same* API call already being made, so capturing size
     costs zero additional S3 requests.
  4. **Existing key index found insufficient.** The only pre-built index reachable
     (`chrisScripts/singleNCFtest/keys_partitioned_year` on mothership) covers a single
     network (`CI`, 199 stations) — a leftover from an earlier single-pair test, not a
     usable stand-in for the full 2,000-station, many-network set. A fresh full-bucket
     scan was required.
  5. **Offline distance-band sweep (no AWS needed).** Using only the already-committed,
     fixed `fps_stations.csv` (2,000 stations, unchanged) and `build_pairs.py`'s own
     `legal_pairs_within_band()`, swept the distance band with station selection held
     constant:

     | Band (km)  | Geometric pairs |
     |------------|-----------------|
     | 110-1,110 (current) | 40,170 |
     | 110-1,500  | 64,539 |
     | 110-2,000  | 99,700 |
     | 110-3,000  | 177,751 |
     | 50-3,000   | 177,751 |
     | 50-5,000   | 361,360 |

     Confirms real headroom to relax connection length alone, well before touching
     station count. Shared-days pass rate at each band is not yet known — needs the
     full key index (item 6).
  6. **Full-bucket key-index rescan launched on mothership** (`~/ncf_metadata3_run/`,
     `build_key_index.py --outdir keys_partitioned_year_full --workers 20`, backgrounded
     via `nohup`+`disown`, isolated from mothership's existing dirty `main`-branch
     working tree). 513 networks discovered. Cost estimated in advance at ~$0.50 (LIST
     requests only, $0.005/1,000, no `GetObject`/egress at all). Progress was
     non-uniform — fast through small networks, then slowed sharply on a few very large
     ones (one alone had 142,701 keys) — consistent with genuine work, not a hang.
  7. **ROVER confirmed as a real, working, zero-AWS-cost alternative** (direct test, not
     assumed from docs): `service.earthscope.org/fdsnws/dataselect/1/query` returns
     real miniSEED data with **zero authentication** required (plain HTTPS, tested via
     both raw `curl` and ROVER itself). `rover download` (the low-level primitive) ran a
     full retrieve -> ingest -> index cycle successfully for `IU ANMO 00 LHZ
     2012-01-01`, producing a real 198,656-byte file and indexing it into ROVER's own
     `data/timeseries.sqlite` — confirms the restart/resume database functionality
     works as advertised.
  8. **Real gotcha found and fixed by direct testing**: ROVER's higher-level
     `retrieve`/`list-retrieve` commands (what the team's existing `batch_rover.py`
     calls) depend on an `fdsnws-availability` pre-check service. That service is
     **retired** — confirmed `404`/"This service has been retired" at both
     `service.earthscope.org` and legacy `service.iris.edu` (this pip package's built-in
     default). The working fix is to call `rover download` directly instead, which skips
     that dead pre-check entirely — already verified working end-to-end (item 7).
     `batch_rover.py` will need this one change before it works against current
     EarthScope services.
  9. **ObsPy `MassDownloader` — CORRECTED FINDING, now fully confirmed working.**
     First attempt (explicit `providers=["EARTHSCOPE"]`) failed with
     `FDSNNoServiceException` — that specific constructor does eager single-provider
     service discovery, which errors out. This was **my own test's mistake, not a real
     EarthScope-side problem** — traced by comparing against the lab's actual working
     implementation (`PrjXX_SAmericaNoise/3_Src/4_download_mdl_slurm/
     download_missing_data_slurm_script.py`, found via user pointer 2026-09-23), which
     calls bare `MassDownloader()` with **no explicit provider restriction**. Retested
     with that exact pattern (plus fixing an unrelated local `certifi`/SSL gap in the
     test venv): **fully successful** — `IU ANMO`, 2018-01-01, real data returned via
     the `EARTHSCOPE` client from `MassDownloader`'s full provider pool (which also
     tries IRISDMC, SCEDC, RESIF, IPGP, etc., silently skipping ones without the
     requested data). Notably **better** than the ROVER test: pulled all 12 available
     channels (6.4 MB total) *and* a full StationXML (128,954 bytes) automatically —
     `MassDownloader` fetches instrument-response metadata as part of its normal
     operation, which `pipeline_test.py`'s simplified preprocessing (Stage 3) currently
     skips entirely. `MassDownloader` does not depend on the retired
     `fdsnws-availability` service at all, so it was never actually affected by that
     issue — a real structural advantage over ROVER's `retrieve`/`list-retrieve` path.

RESULTS
  Full report (once the mothership scan completes): see
  `src/wavenet_pipeline/00_waveform_acquisition/metadata3/DATA_AVAILABILITY_AND_COST_REPORT.md`
  and `src/wavenet_pipeline/00_waveform_acquisition/metadata3/key_index_summary/` (station-day-byte
  summary, the "map of connected days" daily-coverage dataset/figure, and per-pair size
  estimates for the fixed 2,000-station set at the current and relaxed distance bands).

  Two acquisition paths are both real and viable, per the PI's own framing:
  **AWS zero-egress (fast, has real $ cost)** vs. **ROVER-via-plain-FDSN (confirmed
  working, effectively zero AWS $ cost, slower / less parallel as currently written)**.

HARDWARE TIER LOG
  | Tier            | Status      | Date       | Job ID | Log link |
  |------------------|-------------|------------|--------|----------|
  | axon-1 (local)    | done        | 2026-09-23 |  n/a   | code edits, offline sweep, ROVER/ObsPy tests, full connectivity/coverage-density analysis (`build_key_index_summary.py`, `build_connectivity_analysis.py`, `build_coverage_density_analysis.py`) — see `metadata3/DATA_AVAILABILITY_AND_COST_REPORT.md` |
  | mothership         | done        | 2026-09-23 |  PID 52280 (nohup) | Full S3 key-index scan complete: 513 networks, 60,125 stations, 35,026,531 records, 1.09 PB total archive, ~3h build time. Index (1.8GB) committed to repo at `metadata3/keys_partitioned_year_full/` (see its own README) |
  | terravibranium      | not started |            |        | planned for Stage 2's full AWS-vs-ROVER benchmark (PI guidance, 2026-09-23): run the head-to-head comparison on terravibranium (largest local storage/compute), not mothership |
  | Bluehive             | not started |            |        | planned scale-out tier if terravibranium alone isn't enough for the full benchmark; only packaged/processed HDF5 should egress back to terravibranium, not raw waveforms |

DECISION
  - Station selection stays fixed (Stage 0); only distance/duration parameters are open
    to relaxation.
  - **FINAL (PI, 2026-09-23): ROVER retired as a candidate.** ObsPy `MassDownloader` is
    the chosen acquisition tool — works today with no fix, fetches StationXML
    automatically, structurally unaffected by the `fdsnws-availability` retirement that
    broke ROVER's `retrieve`/`list-retrieve`. AWS zero-egress remains documented as a
    real alternative (see cost comparison in the report) but is not the prioritized
    path either.
  - **Bluehive is the prioritized execution environment**, since a working
    `MassDownloader` implementation already exists there
    (`PrjXX_SAmericaNoise/3_Src/4_download_mdl_slurm/download_missing_data_slurm_script.py`)
    — extend that script rather than building new tooling or using terravibranium's
    ROVER-based `Prj_terraSeis` setup.

OPEN QUESTIONS FOR PI
  - Once the full key index and cost report are in hand: which distance-band/duration
    combination to commit to for the actual pair network (current 110-1,110km/90-365
    days, or one of the relaxed candidates)?
  - Timing/scope of the Stage 2 terravibranium (+ Bluehive if needed) AWS-vs-ROVER
    benchmark — note `CLAUDE.md`'s standing rule to check `uptime` and avoid daytime-hours
    intensive jobs on terravibranium before launching it.
  - Worth resolving the ObsPy SSL/discovery issues, or is the confirmed ROVER
    `download`-command fix sufficient and ObsPy not needed?

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
