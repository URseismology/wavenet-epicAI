#!/usr/bin/env python
"""Pre-stage raw SEED to terravibranium in parallel. COPIES ONLY -- never deletes anything.

WHY. The inspector is the only thing that frees scratch, and it is serial: one rsync at a
time. Measured 2026-11-17 on BH3: a single stream moves 66.6 MB/s over a 200 Gb/s link --
0.3% of capacity. The drain is stream-limited, not link-limited, so 27 TB would take ~118
hours. The link is not the problem; doing one station at a time is.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT DO. It runs the SAME rsync the inspector
would run, to the SAME destination, for stations the inspector has not reached yet -- as a
job array, one stream per task. When the inspector later reaches a pre-staged station its
rsync finds the data already there, returns in seconds, and it proceeds to its own remote
file-count/byte verification and purge exactly as before.

**Every safety gate stays with the inspector.** This script never calls rmtree, never writes
inspector state, never decides a shard is good. The worst case for a station it stages
wrongly is a wasted copy -- the failure mode stays "kept twice", never "lost once"
(PIPELINE_PRINCIPLES §5, §6).

AVOIDING COLLISION WITH THE INSPECTOR. The inspector sorts BIGGEST-FIRST while scratch is
tight; this sorts SMALLEST-FIRST and skips the --reserve-top largest stations outright, so
the two work from opposite ends of the same list. A per-station lock directory on shared
scratch keeps array tasks off each other. If both do touch one station the result is a
count mismatch, which makes the inspector HOLD it and retry -- not lose it.

    prestage_archive.py --root ROOT --archive-host H --archive-root D \
                        --shard k --of N [--max-gb 500] [--dry-run]
"""
import argparse
import errno
import glob
import json
import os
import subprocess
import sys
import time

GB = 1024.0 ** 3


def eligible(root):
    """Stations whose raw the inspector could purge. A cheap pre-filter, not a safety gate.

    Staging a station the inspector later rejects costs bandwidth and nothing else, so this
    deliberately does not re-implement verify_shard -- duplicated verification logic drifts,
    and there must be exactly one authority on whether a shard is good.
    """
    state_path = os.path.join(root, "state", "inspector_state.json")
    verified = set(json.load(open(state_path)).get("verified_idx", [])) \
        if os.path.exists(state_path) else set()
    flagged_path = os.path.join(root, "state", "inspector_flagged.json")
    flagged = set(json.load(open(flagged_path))) if os.path.exists(flagged_path) else set()

    out = []
    for rf in sorted(glob.glob(os.path.join(root, "results", "*.json"))):
        try:
            r = json.load(open(rf))
        except Exception:
            continue
        idx = r.get("idx")
        if idx in verified or str(idx) in flagged or not r.get("package_ok"):
            continue
        if r.get("n_days_refused"):
            continue
        key = "{}.{}".format(r.get("network"), r.get("station"))
        shard = r.get("packaged_h5_path") or os.path.join(root, "packaged_h5", key + ".h5")
        if not os.path.exists(shard):
            continue
        raw = r.get("raw_seed_kept_at") or os.path.join(
            root, "scratch_work", key.replace(".", "_", 1), "mseed")
        if not os.path.isdir(raw) or not os.listdir(raw):
            continue
        # Size from the result JSON, NOT an os.walk. Walking all 1,466 raw trees costs
        # >10 min of metadata IO and every array task would repeat it before moving a
        # single byte. `download_bytes` is what the inspector itself sorts on.
        out.append((r.get("download_bytes") or 0, key, raw))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--archive-host", required=True)
    ap.add_argument("--archive-root", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--reserve-top", type=int, default=12,
                    help="leave the N largest to the inspector, which takes biggest-first")
    ap.add_argument("--only-top", type=int, default=0,
                    help="invert: stage ONLY the N largest. The inspector's serial walk "
                         "through the giants is the critical path once the small stations "
                         "are staged, and a collision here is self-correcting -- if the "
                         "inspector rsyncs a station mid-stage its remote file count will "
                         "not match, so it HOLDS and retries. Never a deletion.")
    ap.add_argument("--skip", default="",
                    help="comma-separated station keys to leave alone (e.g. the one the "
                         "inspector is rsyncing right now)")
    ap.add_argument("--max-gb", type=float, default=0.0,
                    help="stop this task after staging roughly this many GB (0 = no limit)")
    ap.add_argument("--cipher", default="aes128-gcm@openssh.com",
                    help="ssh cipher; gcm is markedly faster than the default on this silicon")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cand = eligible(args.root)
    print("[prestage] eligible stations: {:,}".format(len(cand)), flush=True)

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    if skip:
        cand = [c for c in cand if c[1] not in skip]
        print("[prestage] skipping by request: {}".format(", ".join(sorted(skip))), flush=True)

    sized = sorted(cand)                           # smallest first, by recorded bytes
    if args.only_top:
        sized = sized[-args.only_top:]
        print("[prestage] ONLY the {} largest ({:,.0f} GB)".format(
            len(sized), sum(b for b, _, _ in sized) / GB), flush=True)
    elif args.reserve_top and len(sized) > args.reserve_top:
        reserved = sized[-args.reserve_top:]
        sized = sized[:-args.reserve_top]
        print("[prestage] reserving {} largest for the inspector ({:,.0f} GB)".format(
            len(reserved), sum(b for b, _, _ in reserved) / GB), flush=True)

    mine = [s for i, s in enumerate(sized) if i % args.of == args.shard]
    print("[prestage] shard {}/{}: {:,} stations, {:,.0f} GB".format(
        args.shard, args.of, len(mine), sum(b for b, _, _ in mine) / GB), flush=True)

    lockdir = os.path.join(args.root, "state", "prestage_locks")
    try:
        os.makedirs(lockdir)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    staged_b = 0
    t0 = time.time()
    for b, key, raw in mine:
        lock = os.path.join(lockdir, key)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, "{} {}\n".format(os.environ.get("SLURM_JOB_ID", "-"),
                                          time.strftime("%Y-%m-%dT%H:%M:%S")).encode())
            os.close(fd)
        except OSError as e:
            if e.errno == errno.EEXIST:
                continue                           # another task owns it
            raise

        dest = "{}/{}".format(args.archive_root.rstrip("/"), key)
        cmd = ["rsync", "-a", "--partial",
               "-e", "ssh -o BatchMode=yes -c " + args.cipher,
               raw.rstrip("/") + "/", "{}:{}/".format(args.archive_host, dest)]
        if args.dry_run:
            print("[prestage] DRY {:<16} {:>7,.1f} GB  {}".format(key, b / GB, " ".join(cmd[:3])),
                  flush=True)
            os.unlink(lock)
            continue

        t = time.time()
        rs = subprocess.run(cmd, capture_output=True, text=True)
        dt = max(time.time() - t, 1e-6)
        if rs.returncode == 0:
            staged_b += b
            print("[prestage] {:<16} {:>7,.1f} GB in {:>6,.0f}s  {:>6.1f} MB/s  "
                  "(task total {:,.0f} GB, {:.0f} MB/s avg)".format(
                      key, b / GB, dt, b / 1048576.0 / dt,
                      staged_b / GB, staged_b / 1048576.0 / (time.time() - t0)), flush=True)
        else:
            # Leave the lock in place for a failed transfer so a rerun does not thrash the
            # same broken station; the inspector will still handle it correctly either way.
            print("[prestage] {:<16} rsync rc={} {}".format(
                key, rs.returncode, rs.stderr.strip()[:140]), flush=True)

        if args.max_gb and staged_b / GB >= args.max_gb:
            print("[prestage] reached --max-gb {:.0f}, stopping".format(args.max_gb), flush=True)
            break

    print("[prestage] done: {:,.0f} GB in {:,.0f}s".format(staged_b / GB, time.time() - t0),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
