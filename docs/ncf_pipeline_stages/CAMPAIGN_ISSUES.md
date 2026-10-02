# NCF campaign issue log

Running log of defects and completeness gaps found while a campaign is live, compiled daily
(PI decision, 2026-10-02). Each entry carries what was measured, not what was inferred, so
the completion campaign can act on it without re-deriving anything.

**Status key:** `OPEN` (needs a fix) · `DEFERRED` (real, scheduled for the completion
campaign) · `FIXED` (commit named) · `WONTFIX` (accepted, with reason).

---

## I-1 · Existing shards are shallower than they should be — `DEFERRED`

**Measured 2026-10-02**, all 987 v2 shards read from HDF5: 0 empty, median depth **203
days**, max 11,006. Every one of those stations was downloaded under the year-wide request
window, which silently truncates (see I-2), so their depth reflects the defect and not the
archive.

**Scale:** this is the largest single remaining gain in the dataset — larger than the
stations currently downloading. It is *systematic*, not an edge case: it touches all ~935
stations packaged before `9844584`.

**Why deferred, not done now:** recovering the depth means re-running stations that
currently report `package_ok`, which rewrites existing shards. PI, 2026-10-02: revisit
during the completion campaign.

**What it needs when picked up:** explicit authority to overwrite existing shards, then
clear those stations' result JSONs so the orchestrator stops skipping them (it exits early
on any prior result with `package_ok`, orchestrator.py:388). Raw SEED is retained, so much
of this may re-package without re-downloading.

---

## I-2 · Year-wide download windows silently truncate — `FIXED` (`9844584`)

Measured against `JM.YHJB`, independently proven to hold data (plain dataselect GET for
2015-06-01: 555,008 bytes, HTTP 200), varying only the window length with the orchestrator's
exact Restrictions and domain:

| window | expected files | delivered |
|---|---|---|
| 1 day | 3 | **3** |
| 1 week | 21 | 3 |
| 1 month | 93 | 24 |
| 3 months | 276 | 39 |
| 1 year | 1098 | **0** |

`chunklength_in_sec=86400` was not enough — it chunks the download, but ObsPy still resolves
availability across the whole window at once, and with EarthScope's `fdsnws-availability`
retired MassDownloader falls back to "unreliable availability" over that span, then reports
"No data available for request" per interval and raises nothing.

Same defect existed in v1, only wider (one call spanning decades). `2b74f3a` narrowed it to
a year and was credited as a fix; the loss got smaller rather than going away.

**Fixed** by requesting one day at a time, bounded to discovery's real channel epochs.
Confirmed at scale: 45,541 raw files in 10 minutes across 270 tasks.

---

## I-3 · ~109 stations: provider routes them but returns no data — `OPEN`

Discovery routes these to a provider with a stated channel-epoch count, the download returns
0 bytes, and nothing raises. Not concurrency (12 rerun strictly sequentially: still 0), not
routing (identical in both discovery manifests), not the launch path (12 rerun through
`master.py submit`: still 0), not code drift (deployed md5s match the repo).

At least one (`XW.LIRA`) recovered once requests went day-granular — 0 → 264 files — so some
fraction of this class is I-2 and will resolve on its own. Re-count after the campaign
before investigating further.

**Deliberately not chased now** (PI, 2026-10-02): edge cases are not the priority; most
stations are.

---

## I-4 · 16 stations downloaded then refused for missing response — `OPEN`

12.2 GB downloaded, then correctly refused rather than packaged in raw counts
(`inventory_source: "NONE -- no response available"`). The refusal is right — see principle
5, never package data you cannot correct. The gap is that Stage 1.5 metadata coverage was
82% at launch, so the shortfall was known and accepted.

**Fix:** re-run `fetch_station_metadata.py` for these stations, then re-package from retained
raw SEED. No re-download needed.

---

## I-5 · `CHANNELS` still duplicated across two files — `OPEN`

Defined independently in `orchestrator.py` and `fetch_station_metadata.py`. This exact class
of duplication has caused five separate same-day defects (principle 12). It has not bitten
here yet, which is the only reason it is still open.

---

## Method note — how these numbers are produced

Use `campaign_health.py`, never result JSONs. On 2026-10-02 a healthy campaign was reported
as a near-total failure and cancelled, on three compounding measurement errors: a span ratio
whose denominator came from open-ended epochs (`end_utc` = 2599-12-31, so every station read
~0%), a population silently narrowed to the residual hard tail and then stated as the whole,
and a verdict reached without ever opening an HDF5 file.

The order matters and is baked into the tool: crudest global observable first (shards, bytes)
against a known-good reference campaign, before any per-station analysis. v2 held 89% of v1's
bytes the entire time it was being called a failure.
