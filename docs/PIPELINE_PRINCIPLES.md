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

## 11. When a defect appears right after you changed something, audit your own change first.

The costliest hour of 2026-10-01 was spent here. The corrected campaign launched at 01:33
and by 02:30 its output was 3.6% complete, with 75% of stations missing components. Four
mechanisms were hypothesised and tested in turn — `location_priorities=["*"]`, reuse of one
`MassDownloader` across the year loop, provider throttling at 48-way concurrency, and
scratch quota. **All four were wrong**, and each cost a download cycle to disprove.

The actual cause was a one-line gap in a change made six hours earlier. Pinning the
downloader to the federator's endpoint was applied to the initial construction and **not to
the rebuild inside the retry loop**, 130 lines further down:

```python
mdl = MassDownloader()          # retry path -- no providers, falls back to federated discovery
```

106 of 110 stations hit a retry, so nearly all of them silently finished on the unpinned
path. `TA.N49A` downloaded 39 files in 78 s and reported `package_ok=True`; the pinned path
fetches 3,696 files across all six channels.

**Apply:** before theorising about ObsPy, the provider, or the cluster, `grep` every call
site of whatever you most recently touched. A plausible external mechanism is far more
expensive to disprove than your own diff is to re-read.

## 12. A fix applied at one call site is not applied.

This is principle 3's sibling and it recurred five separate times in one day:

- `CHANNELS` widened in `orchestrator.py`, not in `fetch_station_metadata.py` → 77 stations
  could never obtain a response.
- Stage 1.5 built and consumed, never produced by `master.py` → 36.2% raw counts.
- The non-integer-rate `resample` guard present in terravibranium's packager, absent in
  BH3's orchestrator → 54 channels on a wrong time grid.
- Provider pinning applied at construction, not at retry → the campaign above.
- `resample(1.0)` ported verbatim across a different obspy/scipy pair → crashed every day
  of every affected station.

**Apply:** when changing a constant, a client construction, or a selection rule, grep the
whole acquisition tree for other uses *before* committing. Prefer one shared definition
over a correct copy.

## 13. Bound the test, not the dataset.

`orchestrator.py` has `WAVENET_START`/`WAVENET_END` and `WAVENET_DAY_START`/`DAY_END`
precisely so a test can cover a week. They went unused for most of a day: canaries
downloaded entire multi-year station histories to answer questions five days would settle —
`3D.MM05` pulled 32 GB to demonstrate it returns three components, and `7B.SA01` fetched
104 days to show `units='m'`. Bounded, the same checks ran in minutes.

Related: pick test windows **from the discovery manifest's real epochs**. Guessing a year
produced three separate zero-byte runs that looked like failures and were not.

**Apply:** every verification run gets an explicit window. If a test needs a station's whole
history, say why.

## 14. A check that cannot fail cleanly will produce false findings.

The hour-one sampler reported 14 of 14 stations with findings. Two of its checks were wrong:

- It flagged an "all-zero day" on every station because it inspected only the first hour of
  each covered day, and first days often start late. `N4.V58A` day 0 has 15,900 nonzero
  samples of 86,400; days 1-4 have 86,399.
- It compared packaged components against a station's **all-time** channel availability
  rather than the window actually downloaded, which would have been wrong had epochs
  differed.

The real defect was buried among the false ones, which delayed recognising it.

**Apply:** validate the validator against a known-good case before trusting a failing
result. A checker that reports 100% failure is more likely broken than the pipeline is.

## Review checklist for any acquisition-stage change

- [ ] If this produces nothing, can the caller tell "service said no" from "we failed to ask"?
- [ ] Does anything **force** this to run, or does a human have to remember?
- [ ] Is any provider/channel/endpoint list hardcoded, and duplicated in another stage?
- [ ] Can this write a product that is known-bad? Does it fail instead?
- [ ] Is there a post-production check that would catch this recurring?
- [ ] Has it been verified against the artifact (amplitude, inode, bytes) or only the flag?
- [ ] Is the changed code isolated from the running code, with checksums recorded?
- [ ] Have you grepped **every** call site of what you changed, not just the one you edited?
- [ ] Does your verification run have an explicit bounded window, with the epoch taken from
      the discovery manifest rather than guessed?
- [ ] If a check reports everything failing, have you validated the check itself first?

## How long this took, and why

Recorded because the time cost is the argument for the checklist above.

| | |
|---|---|
| Measuring the delivered network, finding 910 "no data" stations | ~1 h |
| Diagnosing it (obspy's `http://` vs `https://`, federator routing) | ~1 h |
| Finding the raw-counts defect and that Stage 1.5 was never wired | ~1.5 h |
| Dead channels, sampling rates, guards, canaries | ~2 h |
| **Diagnosing a one-line gap in my own change** | **~2 h, four wrong hypotheses** |

The last row is the outlier, and it is the cheapest one to have avoided: the answer was a
`grep` of the function I had edited that morning. Every other defect that day was someone
else's unwired fix or a genuine external surprise; that one was self-inflicted and then
self-obscured by looking outward first.

The second largest avoidable cost was unbounded test downloads — hours of wall-clock spent
fetching multi-year histories to answer questions that five days of data settle.

## 15. Measure and log before you patch. A speculative fix destroys the baseline.

When production breaks, the first move is to MEASURE and LOG, never to patch. A fix deployed
into a running campaign without a hypothesis, a test and a verification trail is bad form even
when it is harmless, because it removes the clean baseline needed to find the real cause.

**Incident, 2026-10-05.** 182 campaign tasks were OOM-killed. Within minutes two changes went
into `orchestrator.py` mid-campaign (`RECYCLE_EVERY=365`, `del mdl`) on a hypothesis that was
then disproved by its author's own tests. Neither could corrupt data, but both changed the
memory profile, so every future RSS measurement must be split by whether a task started before
or after that deploy — and tasks within one job span both. The cause is still unknown and is
now harder to establish than before the "fix".

**Mid-campaign is not the problem; unverified is.** The same day, two other mid-campaign
changes were legitimate because each carried a trail: the slurm repoint was proven by a
six-station canary showing `[discovery] pinned` before the full launch, and the response
fetcher fix was verified on three known-bad stations before deploy.

**If the bleeding must be stopped now**, prefer a CONFIGURATION mitigation that leaves code
untouched — more memory, fewer concurrent tasks — because configuration is trivially
reversible and does not confound the diagnosis. And once speculative changes are already in,
resist adding a corrective: a third code state makes the measurement worse, not better.
Record the confound with its deploy time instead.

## 16. Production changes only on a verified diagnosis — the five conditions

The premise, PI 2026-10-05: *"Code always works, until a bug is found. There are always bugs.
Just bugs that haven't yet been found. They will be found, code will be fixed. Code will work,
until..."*

So these rules are not an attempt to prevent bugs. That is not on offer. They govern the
RESPONSE to a bug, because the response is where you either keep or destroy your ability to
understand the system. The year-window defect sat in this pipeline from v1 through two
campaigns that were each credited as fixes; it surfaced eventually, as it always would. What
mattered was that the response preserved enough evidence to find it.

### The rule

A production change requires ALL FIVE:

| | |
|---|---|
| **a** | an **observed failure in production** — not a suspicion, not a code smell |
| **b** | a **falsifiable hypothesis naming the mechanism**, written down BEFORE the fix |
| **c** | that hypothesis **verified against evidence**, with the obvious alternatives ruled out |
| **d** | a fix **verified in isolation** against the failing case, exercising the dimension that failed |
| **e** | **scope limited** to what the hypothesis implicates |

Missing any one: **log it**, and if the bleeding must be stopped, **mitigate by configuration**.

This refines the earlier "production changes only on a FAILED test", which made a failure
SUFFICIENT. It is not. On 2026-10-05 a genuine failure (182 OOM-killed tasks) was treated as
licence to patch; the failure was real and everything after it was not.

### Corollaries

1. **A failure authorizes an INVESTIGATION, not a change.** This is the condition that was
   missing and the one that was violated.
2. **Plausible is not verified.** PI: there is no such thing as plausibility — only correct
   logic or incorrect data. Three plausible explanations for the OOM were offered and all
   three were disproved by their own author's tests.
3. **If the cause cannot be established, mitigate by CONFIGURATION, never by code.** Memory,
   concurrency, walltime: reversible, and they do not confound the diagnosis.
4. **Preserve the failing state until it is measured.** A fix deployed before measurement
   destroys the baseline. This is why the speculative OOM changes were reverted rather than
   kept (R-11).
5. **Verify in the dimension that failed.** R-1 and R-11 both hid because every test differed
   from production in exactly the dimension that mattered — short windows, small stations.
6. **Verified at one call site is not verified everywhere.** Grep the diff's call sites; this
   recurred five times in a single day (principle 12).
7. **A revert needs less justification than a patch.** Reverting restores a verified state;
   patching creates an unverified one. Let the asymmetry bias you toward reverting.

### Scope

This is the rule DURING AN ACTIVE CAMPAIGN. Between campaigns, ordinary development applies:
changes go through the normal isolated-test cycle and do not need a production failure to
justify them.
