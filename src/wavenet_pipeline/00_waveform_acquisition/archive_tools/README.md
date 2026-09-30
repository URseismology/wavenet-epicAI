# archive_tools — two different jobs, do not conflate them

This directory holds two unrelated tool families that happen to both move data off
BlueHive3 scratch. They solve different problems and must not be substituted for one
another.

## `archive_move.py` — move and DELETE stale/superseded data

One-shot: copy -> verify -> record -> delete. Built to free BH3 scratch quota by
removing data nobody needs in place anymore (pre-patch snapshots, data superseded by a
schema fix, etc.). See its own docstring for full usage, the copy->verify->record->delete
discipline, and why the verification strategy differs between `--direct` (rsync) and
relay (tar) modes.

**Never point this at `packaged_h5/` for the live NCF campaign** — it deletes the source
after a verified copy, which is the opposite of what the mirror below is for.

## `mirror_to_{repo,atos,terra}.sh` — copy and KEEP, for live campaign output

Recurring, non-destructive mirror of the packaged NCF HDF5 output
(`wavenet_ncf_production_v2/packaged_h5/`) to three durable destinations, per PI
guidance 2026-09-30: repovibranium, atos (the NAS, see caveat below), and
terravibranium. Source is never touched or deleted.

**Each script only mirrors stations whose result JSON already says `package_ok=True`.**
The campaign writes to `packaged_h5/` continuously while running, so a naive
whole-directory copy could catch a station's `.h5` mid-write.

**These are ONE-TIME SNAPSHOTS, not continuous sync.** Each script builds its file list
once when it starts and does not update it. Stations that complete afterward are not
picked up until the script is run again. **Re-running is cheap** — rsync skips
already-transferred files and only fetches the delta — but nothing currently re-runs
these automatically. This needs a cron entry or a standing manual habit; see
`docs/ncf_pipeline_stages/2026-09-30_HANDOFF_pre_pruning.md` §5 for the full writeup
and current status.

Run these **from BH3 itself** (not axon-1) — BH3 has its own direct, working SSH path to
all three destinations, separate from axon-1's.

**Naming caveat**: "atos" here is a physical Synology NAS (`10.17.7.230`, hostname
`dro-mittal`), used only as a mirror destination. It is unrelated to the `atos-orchestrator`
AWS IAM identity documented in `CLAUDE.md` for mothership's EarthScope/AWS work — same
name, different systems.

## Provenance documentation

`PACKAGED_H5_PROVENANCE.md` is deployed as `README.md` alongside the mirrored data at
all three destinations. It states plainly that this is preprocessed waveform data
(instrument response removed, decimated to 1 Hz) — **not** the NCF cross-correlation
product itself, which is future work built from this.
