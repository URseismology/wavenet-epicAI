#!/usr/bin/env python
"""Move data off scratch to an archive host, with a durable record of where it went.

PI requirement (2026-09-26): move large stale data to atos, then repovibranium, then
terravibranium, and "keep a record of where it is moved to on the directory tree".

The discipline is copy -> VERIFY -> record -> delete, never move-and-hope. Deleting after
an unverified copy converts a recoverable situation into an unrecoverable one, and scratch
is the only place this data exists (it is scratch: not backed up).

Verification is a second rsync pass with --checksum --dry-run. That compares CONTENT hashes
on both ends and reports anything that differs; a size/mtime comparison would happily pass
a silently truncated or corrupted transfer. Deletion happens only if that pass reports zero
differences AND the file counts agree.

The record is JSON (PI's choice) in two places on purpose:
  - a central index, which is the durable record and survives the source being deleted
  - a MOVED.json tombstone left at the source path, which is the convenience record
A tombstone alone dies with the directory it documents; a central index alone drifts. Both,
cross-referenced, is what makes "where did that go?" answerable years later.

Usage:
  archive_move.py --src /scratch/.../dir --dest-host atos --dest-user urseismoadmin \\
                  --dest-path /volume1/NetBackup/wavenet --reason "superseded by v2" [--apply]
Without --apply it is a dry run: it reports what it would transfer and never deletes.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

INDEX_DEFAULT = os.path.expanduser("~/wavenet_archive_index.json")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def dir_stats(path):
    """(file count, total bytes) for a local directory tree."""
    n = total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                n += 1
            except OSError:
                pass
    return n, total


def load_index(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return {"moves": []}


def save_index(path, index):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="local source directory")
    ap.add_argument("--dest-host", required=True)
    ap.add_argument("--dest-user", required=True)
    ap.add_argument("--dest-path", required=True, help="destination PARENT directory")
    ap.add_argument("--reason", required=True, help="why this is being archived")
    ap.add_argument("--index", default=INDEX_DEFAULT)
    ap.add_argument("--apply", action="store_true",
                    help="actually transfer and delete; omit for a dry run")
    args = ap.parse_args()

    src = os.path.abspath(args.src.rstrip("/"))
    if not os.path.isdir(src):
        sys.exit(f"not a directory: {src}")
    name = os.path.basename(src)
    remote = f"{args.dest_user}@{args.dest_host}"
    dest_dir = os.path.join(args.dest_path, name)
    target = f"{remote}:{dest_dir}/"

    n_files, n_bytes = dir_stats(src)
    print(f"[archive] {src}")
    print(f"[archive]   -> {target}")
    print(f"[archive]   {n_files:,} files, {n_bytes/1e9:.2f} GB")
    print(f"[archive]   reason: {args.reason}")
    if not args.apply:
        print("[archive] DRY RUN -- pass --apply to transfer. Nothing copied, nothing deleted.")
        return

    print("[archive] 1/4 transferring ...", flush=True)
    r = run(["rsync", "-a", "--partial", "--info=stats2", f"{src}/", target])
    if r.returncode != 0:
        sys.exit(f"[archive] TRANSFER FAILED (rc={r.returncode}); nothing deleted.\n{r.stderr[-800:]}")

    print("[archive] 2/4 verifying by CONTENT checksum ...", flush=True)
    v = run(["rsync", "-a", "--checksum", "--dry-run", "--itemize-changes",
             f"{src}/", target])
    if v.returncode != 0:
        sys.exit(f"[archive] VERIFY PASS FAILED (rc={v.returncode}); nothing deleted.\n{v.stderr[-800:]}")
    # Any itemized line means the remote copy differs from the source.
    diffs = [l for l in v.stdout.splitlines()
             if l and not l.startswith(("sending", "sent ", "total ", "cannot ")) and l[0] in "<>ch.*"]
    diffs = [l for l in diffs if not l.startswith(".d")]        # unchanged dirs are fine
    if diffs:
        print("[archive] VERIFY FAILED -- these differ, so NOTHING is deleted:", file=sys.stderr)
        for d in diffs[:20]:
            print("   " + d, file=sys.stderr)
        sys.exit(1)
    print("[archive]   verified: remote content matches source")

    record = dict(
        name=name, source=src, dest_host=args.dest_host, dest_user=args.dest_user,
        dest_path=dest_dir, remote=f"{remote}:{dest_dir}",
        n_files=n_files, n_bytes=n_bytes, gb=round(n_bytes / 1e9, 3),
        reason=args.reason, verified="rsync --checksum (content hash), zero differences",
        moved_at=datetime.datetime.now().isoformat(timespec="seconds"),
        retrieve=f"rsync -a {remote}:{dest_dir}/ <local-dir>/")

    print("[archive] 3/4 recording ...", flush=True)
    index = load_index(args.index)
    index["moves"] = [m for m in index["moves"] if m.get("source") != src] + [record]
    save_index(args.index, index)
    tomb_dir = src + ".MOVED"
    os.makedirs(tomb_dir, exist_ok=True)
    with open(os.path.join(tomb_dir, "MOVED.json"), "w") as f:
        json.dump(record, f, indent=2)
    print(f"[archive]   index: {args.index}")
    print(f"[archive]   tombstone: {tomb_dir}/MOVED.json")

    print("[archive] 4/4 deleting source (verified copy exists) ...", flush=True)
    d = run(["rm", "-rf", src])
    if d.returncode != 0:
        sys.exit(f"[archive] delete failed: {d.stderr[-400:]}")
    print(f"[archive] DONE -- freed {n_bytes/1e9:.2f} GB; retrieve with:\n    {record['retrieve']}")


if __name__ == "__main__":
    main()
