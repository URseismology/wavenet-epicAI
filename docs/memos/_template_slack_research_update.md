Template for Slack-style research updates to the team (wavenet_junior, wavenet_senior).
Distinct from the detailed memos in this directory (see `2026-09-23-...md` and
`2026-09-30-...md` for that format — TL;DR / tasks / end-notes, meant to be read in
full). This is the short-form companion: something the PI posts directly to Slack,
linking out to a detailed memo for anyone who wants the full depth.

Written entirely in first person, PI to team — never "I verified" as if reporting
upward, never hedging about the PI's own methodology. State findings and decisions
plainly, the way a lead states facts to people who work for them. Structure below,
demonstrated with a real worked example rather than fill-in-the-blank placeholders,
because tone is the hard part to get right and an example teaches it faster than a
description would.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Link to the full memo first, one line.
2. THE TASK — one or two sentences, what this workstream is actually for.
3. TOP DESIGN GOAL — the one thing that governs every other decision in this
   workstream, stated with the real number/evidence behind it, not just asserted.
4. CAMPAIGN HISTORY AND STATUS — how this got here (decisions made, when, why) and
   where it stands right now. This is what lets someone who missed the last update
   re-orient in one paragraph instead of asking "wait, what happened to v1?"
5. BUGS FOUND, STATE, ETA — every real bug, with the actual numbers (how many affected,
   what the wrong value was, what it's fixed to, and — critically — whether the fix was
   verified to work or is still an assumption). ETA given as a range with the reason
   it's volatile, never a single confident number if the real data doesn't support one.
6. WHAT GETS DELIVERED — final size/shape if nothing else goes wrong, and current
   backup/redundancy state, called out as done vs. still-needed separately.
7. COST AND RESEARCH VALUE — real cost (or genuinely $0, say so), and what this
   actually buys the project, in concrete downstream terms, not "this will be useful."
8. Named asks per person, each tied to their actual role, pointing to the detailed
   memo for exact commands/specifics rather than repeating them here.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WORKED EXAMPLE (posted 2026-09-30, #wavenet-epicai)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Full writeup: https://github.com/URseismology/wavenet-epicAI/blob/main/docs/memos/2026-09-30-ncf-v2-campaign-bugs-recovery-and-backup.md

**The task**: build a real, globally-distributed ambient-noise waveform dataset —
download from a fixed 2,000-station network, package into clean HDF5, as the
foundation for computing noise cross-correlation functions and FTAN dispersion
analysis. This is the real-data counterpart to our CPS-synthetic training pipeline,
not a replacement for it.

**Top design goal**: ray-path coverage density and uniformity across the globe, with
limited redundancy — not station count, not pair count. I tested bands from 110 km out
to the full 180° max path length: marginal new-coverage efficiency collapses hard past
~5,000-10,000 km (486 new map-cells per 1,000 pairs at our current 110-1,110 km band,
down to 0.34 at the widest band tested), so our current band is a deliberate efficiency
choice, not a compute-limited one. Full history across all 2,000 stations is 78.9 TB
raw, deduplicated, decimating and compressing down to a design estimate of ~5.0 TB
packaged — that's the number to plan storage against.

**Campaign history and where we are**: I decided on ObsPy MassDownloader over Bluehive
back in late September, after comparing it against an AWS zero-egress path and a
ROVER/FDSN path — Bluehive won on cost (effectively free vs. real AWS dollars) at
similar wall-clock time. That first full run (v1) launched on legacy BlueHive, then I
migrated it to BH3 on 2026-09-29 once I'd measured 2-3x faster throughput there and far
better scheduling (260+ concurrent tasks vs ~46). v1 ran to completion across all 2,002
tasks — but only 668 stations actually came back with real data. The rest either
returned zero bytes or never finished at all. That's what triggered the audit I'm
reporting on below: I went through every one of those failures, found the actual
causes, fixed them, and launched v2 today as the corrected re-run. v2 is live now,
running on BH3, and is what the rest of this update is about.

**Bugs found, and where the campaign stands now:**
- 433 stations had BH/LH channels registered but got zero bytes — `location_priorities`
  was hardcoded to only three codes (`""`, `00`, `10`). In a 25-station sample, 40% had
  all their real data sitting under other codes (01, 02, 30, 31, 32) — usually multiple
  sensors at different depths on one site, not junk. Fixed to accept every code.
- 312 stations had a real seismometer in a band we just weren't requesting
  (HH/EH/SH/MH). Our channel list was `BH?,LH?` only. Widened to six bands,
  cheapest-sampling-rate first.
- 511 of 2,002 tasks were OOM-killed — `--mem-per-cpu` was 2G against nodes that
  actually give ~7.6-9.0 GB per core. Raised to 7G.
- I verified both config fixes actually retrieve data rather than trusting metadata —
  ran a real download test on BH3 and measured 25% recovery (3 of 12) on the
  location-code category and 33% (4 of 12) on the channel-band category, zero
  regressions on stations that already worked.
- 208 stations had no seismometer at the site at all (magnetometers, pressure sensors,
  accelerometers). Found real replacements with confirmed data for 108 of them, within
  0.5° of the original. One station in our locked 2,000 — `XB.ELYH0` — turned out to be
  the InSight lander, on Mars. Deleted, not replaced.
- v2 is re-running 1,339 stations total: 837 from the location/channel fix, 386 from
  the memory fix, 108 replacements, and 8 stations whose already-downloaded data I found
  had gone missing on disk and needed re-pulling. Currently 1,018 of 1,999 stations
  confirmed with data, 96% reported. ETA is genuinely volatile — measured rate has swung
  from 4,542 to 62,631 days/hour depending on whether small or giant stations are in
  flight — so anywhere from 3 days to 3 weeks is the honest range.

**If no more bugs turn up:** ~5.0 TB packaged, final (my own calculation from partial
progress lands lower, around 2.7 TB, but that's because smaller stations finish first —
the design estimate is the one to trust). Backup is mirrored to repovibranium (205 GB /
380 files so far), atos, and terravibranium. Mirroring works but is currently a one-time
snapshot — still need to make it recurring. A merged master gets built on terravibranium
once the campaign completes, backed up to the other two.

**Cost and research value:** $0 direct cost — free FDSN protocol, not the AWS
zero-egress path (I costed that at ~$2,083 for the full dataset). BH3/BlueHive time is
CIRC-allocated with no SU billing, unlike Empire AI. Research value: real,
globally-distributed ambient-noise data, independent of our CPS-synthetic training set —
an out-of-distribution check on the trained U-Net once NCF/FTAN is built on top, and the
shared benchmark dataset for the AkiNet/iRADNet comparison in our broader Empire AI
program.

junior — you have full command of tracking v2, exact commands and the v1/v2 hard-link
rule are in the memo. senior — data structure and the FTAN integration path are in
there too.
