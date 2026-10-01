Record of the 2026-10-01 v2 recovery work: two silent defects found in the live campaign,
their root causes, the fixes, and the re-run design. Written in the same fixed-field style
as the other stage records so it can be reviewed without having watched the work happen.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
V2 RECOVERY — FEDERATED DISCOVERY AND INSTRUMENT RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SUMMARY

  Two independent defects, both of which produced confident, wrong results rather than
  errors. Together they account for roughly half the campaign's usable output.

    A. 910 stations recorded as having no data. 763 of them (84%) demonstrably DO have
       retrievable data. Cause: unpinned MassDownloader client discovery failing silently.
    B. 36.2% of packaged stations were left in raw counts, uncorrectable for amplitude.
       Cause: Stage 1.5 (`fetch_station_metadata.py`) was never run on the v2 root.

  Neither was a design flaw. Defect B in particular was already diagnosed, fixed and
  documented in 2026-09-25_production_migration_decisions.md — the fix simply was not
  executed for this campaign, and nothing in the tooling forced it to be.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A. 910 STATIONS WRONGLY RECORDED AS HAVING NO DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HYPOTHESIS
  The stations really had no data, or the v1 config bugs persisted.

WHAT WAS TRIED
  Both rejected by direct measurement, not reading:
  - The channel/location/memory fixes ARE live and unoverridden in the deployed code.
  - The `RectangularDomain(lat +-0.5, lon +-0.5)` filter is innocent: all 40 sampled
    manifest coordinates fall inside their own box.
  - Reproducing the campaign's EXACT call (full-year window, daily chunks, same
    restrictions) on TA.N49A — a station recorded as empty — downloaded 2,190 files /
    3.66 GB. The code is correct and the data is there.
  - Sampling 25 zero-byte stations against FDSN directly: 23 had a wanted band registered,
    18 returned real waveform samples.

ROOT CAUSE
  `orchestrator.py` built `MassDownloader()` with no pinned providers and reused that one
  instance across every year of a station ("one client-discovery pass reused across all
  years below"). Client initialisation fails routinely even on an idle node — observed
  `Failed to initialize client 'ISC'/'NCEDC'/'KOERI'`. If the provider holding the data
  loses that coin flip at station start, every subsequent year queries a provider set that
  does not have it, ObsPy reports "no data available", and `download()` returns cleanly.
  No exception is raised, so `n_download_retries` stays 0, nothing retries, and the station
  records zero bytes with no error.

  The code comment at orchestrator.py:328 already described this exact failure mode
  ("sometimes silently swallowed inside ObsPy and reported as 'IRIS has no data'").
  Year-chunking fixed the crashing variant; the silent variant survived through a different
  door. The 2026-09-23 memo's own caveat is relevant: concurrency was "confirmed linear at
  least up to 20-way ... not yet confirmed all the way to 120-way". v2 ran at 76-84.

FIX — federated search, pinned download
  New standalone stage `discovery/discover_stations.py`. Per station it asks the IRIS
  federator which data centre holds the data, which channels exist, and over what real
  epochs; the downloader is then pinned to that endpoint and the year loop bounded to those
  epochs, so NO client discovery happens at job time at all.

  Three things learned the hard way, each now encoded in the tool:

  1. obspy's RoutingClient cannot be used. `_split_routing_response()` detects data centres
     with `if "http://" in line`; IRIS/EarthScope now return `https://`, and "http://" is
     not a substring of "https://". obspy therefore parses ZERO data centres and raises
     `FDSNNoDataException: Nothing remains to download after the provider inclusion/
     exclusion filter` — naming a filter that never ran. We parse the federator ourselves.

  2. obspy's provider REGISTRY can point somewhere that cannot serve data. BL.CDCB routes
     to DATACENTER=USPSC; obspy's "USP" shortcut is http://sismo.iag.usp.br, which exposes
     no dataselect service at all. The federator returns http://seisrequest.iag.usp.br,
     which does. Discovery therefore always records the DATASELECTSERVICE URL the federator
     returned, never a registry name. Measured on the 7 USPSC-routed stations: 5 of 7 now
     return real waveforms; under name-based pinning it would have been 0 of 7.

  3. fedcatalog's HTTP 404 is overloaded — it means "nothing matched THIS query", not a
     transport failure. 12.OBS21 returns 404 for the wanted bands but 200 for `cha=*`, with
     5 real channels at the station service. Discovery re-asks with `cha=*` to separate
     "no usable seismometer" from "not there at all". Conflating them turned 147 phantom
     errors into 142 honest NO_WANTED_BAND plus 5 real absences.

  `fdsnws-availability` is dead (HTTP 404 at both IRIS and EarthScope), corroborating the
  ROVER finding in CLAUDE.md, so channel epochs from the federator are the best available
  "what exists" signal.

RESULTS
  910 audited in 34 s: 763 ROUTED, 142 NO_WANTED_BAND, 5 NO_DATA_AT_SERVICE, 0 errors.

    data centre   stations   endpoint used
    IRISDMC            749   https://service.earthscope.org
    GEOFON              27   http://geofon.gfz-potsdam.de
    USPSC                7   http://seisrequest.iag.usp.br
    RESIF/NOA/INGV       3   (one each)
    AUSPASS              1   http://auspass.edu.au  (absent from obspy's registry)

  15 stations route ONLY to a non-IRIS centre, so naive IRIS pinning would have lost them.
  Real year spans cut request volume ~9x against a blind 1970-2026 sweep (5,057 real
  station-years vs 43,491).

CONTRACT CHECK
  Discovery asserts a station has routable channels. If the download then returns zero
  bytes with no year error, that is a contradiction, not a result, and is recorded as
  `discovery_contradiction` in the result JSON. Non-fatal by design, so task exit semantics
  the inspector depends on are unchanged. It caught three separate errors during its own
  development, including a fabricated year range and the dead USP endpoint.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
B. 36.2% OF PACKAGED STATIONS LEFT IN RAW COUNTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW THE ONES THAT WORKED, WORKED — AND WHY
  The split is almost perfectly binary, and it is not about the stations at all:

    origin             response_ok=True   response_ok=False
    carried from v1                 646                  15     (97.7% corrected)
    fresh in v2                      11                 358     ( 3.0% corrected)

    inventory_source           count
    station_metadata            660   (646 ok)
    NONE                        352   (all failed)
    massdownloader_sidecar       18   (11 ok)

  v1's root has 1,479 StationXML files in `station_metadata/`. v2's root had no
  `station_metadata/` directory at all. Carried-forward stations inherited v1's responses;
  everything processed fresh in v2 fell through to the MassDownloader sidecar, which almost
  never survives the waveform path. That is exactly the coupling
  `fetch_station_metadata.py` was built to break.

VERIFIED REAL, NOT A TAGGING PROBLEM
  Amplitude settles it. Sampling a mid-coverage day per station:

    units='m'       RMS median 1.68e-06   (ground displacement, correct)
    units='counts'  RMS median 1.62e+02   (raw digitizer output)

  The groups separate by 8 orders of magnitude, and the pipeline recorded the cause
  honestly (`response_ok=False`, `inventory_source='NONE -- no response available'`).
  Measured 36.2% uncorrected, matching the 36.9% in orchestrator.py's own comment.

ROOT CAUSE
  Process, not design. `master.py` never invokes `fetch_station_metadata.py`, and nothing
  else forces it, so the documented Stage 1.5 and its `--report` gate were simply skipped
  when v2 was initialised. The gate exists precisely to catch this before the preprocessing
  budget is spent: *"The number that must be zero is 'HAS DATA but NO response
  (blocking)'"* (2026-09-25_production_migration_decisions.md).

FIX
  1. Run Stage 1.5 against the v2 root for all 1,999 stations, then the `--report` gate.
  2. New runtime guard in `orchestrator.py`: if waveforms were downloaded but NO response
     is available from either source, the station now FAILS rather than packaging in raw
     counts. PI, 2026-10-01: *"data that is not correct is not useful data ... it has to be
     packaged correctly."* A recorded degradation still silently fills the archive with
     unusable data; a failure leaves the station visibly incomplete and re-runnable.
     Fires only when waveforms exist and no response was found, so the legitimate
     "this site has no seismometer" case is untouched (those stations have no waveforms).
     `WAVENET_ALLOW_RAW_COUNTS=1` overrides for a deliberate exception.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RE-RUN DESIGN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Same campaign, same root — NOT a new campaign. The orchestrator's idempotent skip fires
  only on `package_ok=True`, so the 763 no-data stations re-run cleanly in place, the
  existing inspector purges them as ordinary v2 stations, and no merge is needed.

    763   no-data, discovery-routed
    373   packaged but in raw counts
      7   in both
  = 1,129 stations to re-run

  The 373 require their result JSONs cleared first, or the idempotent skip refuses them.
  Their raw SEED has been purged, so they re-download — accepted deliberately: raw counts
  cannot be rescaled after the fact, and incorrect data is not worth keeping.

  Order of operations, each step gated on the previous:
    1. `fetch_station_metadata.py --root $ROOT --idx N` across all stations.
    2. `fetch_station_metadata.py --root $ROOT --report` — blocking count must be ZERO.
    3. Canary re-run, confirm shards come back `units='m'`.
    4. Release the full 1,129.

OPERATIONAL NOTES THAT COST TIME
  - Indices are row positions in `<root>/manifest/fps_stations.csv`, the SIZE-SORTED copy
    master.py makes at init — NOT `metadata3/fps_stations_v2.csv`. The orders are
    completely different; using the wrong one writes into the wrong station's record.
  - `MaxArraySize = 1001`. Indices go out in 1000-blocks with `WAVENET_IDX_OFFSET`.
  - `--export=NONE` is load-bearing (master.py:400). Submitting from a shell with
    `instaseis` already active causes a double activation that breaks pkg_resources with
    `ImportError: cannot import name '_manylinux'` at `from obspy import ...`.
  - `sbatch --wrap` runs under sh, where `conda activate` silently does nothing and python
    falls back to the broken base env. Use a real job script.
  - QOS submit limit is 2,000 array elements per user. Legacy BlueHive shares
    `/scratch/tolugboj_lab`, so overflow work can be submitted there against the same data.

OPEN QUESTIONS FOR PI
  - Should `master.py init` require Stage 1.5 to have run, so a future campaign cannot skip
    it the way v2 did? The tooling currently permits the omission silently.
  - The 142 NO_WANTED_BAND and 5 NO_DATA_AT_SERVICE stations: confirm these are accepted as
    permanently unavailable rather than pending further recovery.
  - Delivered data that is no longer retrievable (BL.CDCB holds 20 days from 1992 that no
    provider now serves) argues against ever discarding packaged output, even uncorrected,
    until a corrected replacement exists.

APPROVAL LOG
  [ ] Reviewed by PI (tolulope.olugboji@rochester.edu) — date, verdict
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
