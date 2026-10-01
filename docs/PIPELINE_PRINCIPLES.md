# Pipeline principles — read before changing or reviewing any acquisition stage

These are not style preferences. Each one was learned from a defect that silently destroyed
or withheld real data in this project, and each cost days to find because the pipeline
reported success while doing the wrong thing. They apply to any stage of the NCF
acquisition pipeline, and to review of any change to it.

Source incidents are named so the reasoning can be re-checked rather than taken on trust.

---

## 1. Silence is not success. A clean return proves nothing.

The most expensive defects here all reported *no error*.

- 910 stations recorded `download_ok=False, download_bytes=0`, no exception, no retry.
  763 of them had real, retrievable data. ObsPy's MassDownloader had failed to initialise
  the client holding it and reported "no data available", which the pipeline recorded as
  truth (2026-10-01).
- A `set -eo pipefail` test script exited silently mid-run because a `grep` matched
  nothing. Same class of bug, in the test written to catch the bug.

**Apply:** a stage that produces nothing must distinguish *"the service said no"* from
*"we failed to ask"*. Never let the second decay into the first. Where a precondition
asserts data exists, a zero result is a **contradiction** to record and retry, not an
answer to accept.

## 2. No case is benign until retrieval has been exhausted.

PI, 2026-10-01: *"always pursue getting as much data and metadata as possible. no case is
benign until we've exhausted possibility of retrieving data and metadata."*

The coverage gate classified 360 stations as "benign — no response and also no waveform
data". Running federated discovery over them showed **213 were routable with real data**.
All 13 stations marked "unknown" were routable too. 226 stations were one step from being
written off.

**Apply:** "nothing there" is a measurement, not a default. Before recording a station,
channel or epoch as unavailable, exhaust: a different provider, a different channel band,
a different time window, and the federator's own routing.

## 3. A fixed list of providers, channels or endpoints will be wrong.

Three separate incidents, all the same shape:

- `location_priorities = ["", "00", "10"]` discarded real instruments at codes 01/02/30/31/32.
- `CHANNELS = "BH?,LH?"` was widened on the download side and **not** in
  `fetch_station_metadata.py`. All 77 stations the gate flagged as "has data but no
  response" carried only HH/EH/SH/MH and zero BH/LH — the fetcher could never match them.
- `PROVIDER_CHAIN` omits AUSPASS (38 stations), USPSC (37), ICGC, SED, BATS, UIB-NORSAR.
  Worse, obspy's registry entry for `USP` points at a host with **no dataselect service**,
  so a name that *is* in the list can still be the wrong endpoint.

**Apply:** prefer an authoritative lookup (the FDSN federator) over a hardcoded list, and
keep the list as a fallback. When a list must exist, it is a single source shared by every
stage that depends on it — never copied.

## 4. A fix is not deployed until something forces it to run.

`fetch_station_metadata.py` (Stage 1.5) was created, documented and correct from
2026-09-25. `orchestrator.py` was taught to read from it. But no version of `master.py`
ever referenced it — 18 commits, zero mentions — and `init` did not even create the
directory it writes into. v1 had responses only because someone ran the stage by hand; all
1,479 of its StationXML were written in a single hour, minutes after the commit landed.
v2 was a new root, so it had none, and packaged 36.2% of its stations in raw counts.

**Apply:** a stage that must run is created by `init` and **gated** by `submit`. If a
human has to remember it, it will be skipped, and the skip will be silent. Review question:
*what would happen if nobody ran this?*

## 5. Never package data you cannot correct.

PI, 2026-10-01: *"data that is not correct is not useful data. raw packaged SEED is useless.
it has to be packaged correctly."*

Raw counts are not amplitude-comparable across stations, so they cannot be cross-correlated.
Recording the degradation (`response_ok=False`) was not enough — it filled the archive with
unusable shards marked complete, and by the time it was noticed the inspector had purged
the raw they came from, so repair meant re-downloading.

**Apply:** fail the unit of work rather than writing a product that is known-bad. An
explicit failure stays visible and re-runnable; a recorded degradation does not.

## 6. Keep the raw until the product is verified.

The inspector purges verified raw SEED to reclaim scratch. That is only safe once packaging
is trusted. It was not, and the purge turned a re-packaging job into a re-download.

**Apply:** the inspector is **off by default**. Turn it on per-campaign, once that
campaign's packaging has been verified end to end. Be aware some delivered data is no
longer retrievable at all — `BL.CDCB` holds 20 days from 1992 that no provider now serves —
so never discard packaged output until a verified replacement exists.

## 7. Verify on the real artifact, not the label.

`units='counts'` could have been a mislabelling. It was not: sampling mid-coverage days
showed `units='m'` stations at RMS 1.68e-06 and `units='counts'` at 1.62e+02 — **eight
orders of magnitude** apart. The tag was telling the truth.

Equally, a station's success record can belong to a different campaign: 646 of v2's
"successes" are v1 results carried forward, their shards **hard links to v1's files** (same
inode). v2's own response-correction rate was 3%, not 64%.

**Apply:** check the data, the inode, the amplitude — not the flag. When a number looks
good, ask which run actually produced it.

## 8. Test, then keep testing after production.

PI, 2026-10-01: *"perfect case of making sure you test, test, test... and afterwards
continue to test even post production."*

Every defect above was found *after* the campaign was running and reporting healthy. The
36.9% raw-counts rate was measured once, fixed, and then recurred at 36.2% in the next
campaign because nothing re-measured it.

**Apply:** the coverage gate (`fetch_station_metadata.py --report`) and an amplitude spot
check are **post-production** checks, not pre-flight only. Re-run them against a live
campaign periodically. A number that was right once is not right forever.

## 9. Prove the environment, not just the code.

Verified-correct code still failed repeatedly for environmental reasons that produced
confusing errors:

- `--export=NONE` is load-bearing: submitting from an activated conda env causes a double
  activation that breaks `pkg_resources` with `ImportError: cannot import name '_manylinux'`.
- `sbatch --wrap` runs under `sh`, where `conda activate` silently does nothing.
- `set -u` kills the job inside conda's own `gdal-activate.sh`.
- `MaxArraySize = 1001`, so indices above that need `WAVENET_IDX_OFFSET` blocking.
- Indices are rows in `<root>/manifest/fps_stations.csv`, the **size-sorted** copy — not
  `metadata3/fps_stations_v2.csv`. Using the wrong one writes into the wrong station.

**Apply:** when a stage "works by hand but not in production", suspect the environment
before the logic, and record the finding here rather than re-deriving it.

## 10. Isolate changed code so drift is a diff, not an argument.

The live framework and the patched recovery framework are separate trees with checksums
recorded in `VERSIONS.txt`. Exactly one file differs. That made "did a bad patch cause
this?" answerable in seconds — the answer was no, and the audit proved it rather than
asserting it.

**Apply:** deploy changed pipeline code beside the running one, not over it, and record
checksums of both.

---

## Review checklist for any acquisition-stage change

- [ ] If this produces nothing, can the caller tell "service said no" from "we failed to ask"?
- [ ] Does anything **force** this to run, or does a human have to remember?
- [ ] Is any provider/channel/endpoint list hardcoded, and duplicated in another stage?
- [ ] Can this write a product that is known-bad? Does it fail instead?
- [ ] Is there a post-production check that would catch this recurring?
- [ ] Has it been verified against the artifact (amplitude, inode, bytes) or only the flag?
- [ ] Is the changed code isolated from the running code, with checksums recorded?
