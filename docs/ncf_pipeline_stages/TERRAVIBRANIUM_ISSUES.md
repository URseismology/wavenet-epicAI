# terravibranium SAm noise packaging — issue log

**Scope: this pipeline only.** `sam_package_one.py` / `sam_package_driver2.py` packaging ROVER
data from `/RAID6/lab_archive/PrjXX_SAmericaNoise/2_Data/2_RoverDB` into
`/RAID6/lab_archive/packaged_v2`. Separate codebase, storage and station set from the
BlueHive3 campaign — those issues live in `CAMPAIGN_ISSUES.md`.

State as of 2026-10-05: 883 of 896 stations done, 895 shards, 458 GB, **0 empty shards**,
235,005 station-days, all channels at 1.0 Hz. The 13 remaining are in flight, none stalled.

---

## T-1 · 9 stations with raw-counts channels

**Identified** 2026-10-05 · **Status** OPEN · **Severity** medium

South America noise packaging (`/RAID6/lab_archive/packaged_v2`, 895 shards, 427 GB). Same class as the BlueHive3 R-3 defect but an independent codebase, so the fix
does not carry over and must be made separately in `sam_package_one.py`.

| Station | Uncorrected | Days |
|---|---|---|
| `AS.ZOBO` | 2 of 3 | **3,914** |
| `SR.BOCO` | 3 of 3 | **1,220** |
| `XF.HB30` | 6 of 6 | 41 |
| `TC.ICR3` | 3 of 3 | 36 |
| 5 others | — | 1-86 |

`AS.ZOBO` and `SR.BOCO` are the substantial ones. **Repair after that campaign finishes**, and
only once the code-provenance question (R-8) is settled for that pipeline.

---


## T-2 · The packaging code is not version-controlled

**Identified** 2026-10-05 · **Status** MITIGATED (copies made), not resolved

`sam_package_one.py`, `sam_package_driver2.py` and `sam_inventory.json` — which defines the
896-station target set and its ordering — existed **only in `/tmp`** on a host up 406 days.
Not in the repo, not on any branch, not on axon-1.

Copied 2026-10-05 with mtimes preserved to `/RAID6/lab_archive/sam_packaging_code/` (survives
reboot, with a `PROVENANCE.txt` recording md5s and the live `ps` output) and to
`~/sam_packaging_archive/` on axon-1. All 23 files verified byte-identical.

**Still open:** which variant is canonical. `sam_package_one_optB.py` (Sep 30 13:29) is NEWER
than the live script (12:48) and was never put into service; `_bandtest` likewise. Decide after
the campaign finishes, then commit the winner.

## T-3 · Shard provenance cannot be recovered from the data

**Identified** 2026-10-05 · **Status** OPEN

These shards carry **empty group attributes** — no `patch_level`, no embedded StationXML, no
coordinates. So "which script version produced this shard" is unanswerable from the files.

It cannot be inferred reliably either: the live script was edited at least twice mid-campaign
(`.bak_precache` Sep 30 10:52, `.bak_allchannel` Sep 30 12:48) while stations were completing,
and per-file checkpointing means a station interrupted before an edit and resumed after it has
output from **both** code versions inside one shard.

**Do not claim a provenance conclusion without a deep comparison after the campaign ends**
(PI, 2026-10-05). The live `ps` capture in `PROVENANCE.txt` establishes only what was running
at that one instant, not what produced the 868 stations already finished.
