#!/usr/bin/env python
"""Move data off scratch to an archive host, with a durable JSON record of where it went.

PI requirement (2026-09-26): move large stale data to atos, then repovibranium, then
terravibranium, and "keep a record of where it is moved to on the directory tree".

Discipline: copy -> VERIFY -> record -> delete. Never move-and-hope. Deleting after an
unverified copy turns a recoverable situation into an unrecoverable one, and scratch is
the only place this data exists (it is scratch: explicitly not backed up).

WHY A RELAY. The obvious `rsync src dest` does not work here, established by testing:
Bluehive (where the data is) cannot reach either NAS -- they sit on the private 10.17.x
lab network -- and Bluehive<->terravibranium is not authenticated in either direction.
axon-1 is the only host that can reach both, but its /usr/bin/rsync is Apple's openrsync
(macOS 26), which fails against these rsync daemons. So the transfer streams tar over two
ssh hops through axon-1, which needs no new credentials and was verified end to end.

WHY A MANIFEST. A tar stream gives no built-in integrity check, so verification compares
per-file md5 manifests generated independently on BOTH ends. That catches a truncated or
corrupted stream, which a file-count or byte-total comparison would silently pass.

THE RECORD is written twice on purpose: a central JSON index (durable -- it survives the
source being deleted) and a MOVED.json tombstone beside the source (convenient -- it is
found by whoever goes looking in the old location). A tombstone alone dies with the
directory it documents; an index alone drifts out of date. Each record carries the exact
command to retrieve the data again.

Usage:
  archive_move.py --src-host bluehive --src /scratch/.../dir \\
                  --dest-host atos --dest-user urseismoadmin \\
                  --dest-path /volume1/NetBackup/wavenet_archive \\
                  --reason "superseded by schema v2" [--apply]
Without --apply nothing is transferred and nothing is deleted.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

INDEX_DEFAULT = os.path.expanduser("~/wavenet_archive_index.json")
SSH = ["ssh", "-o", "BatchMode=yes"]


def sh(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def remote(host, command, timeout=None):
    return sh(SSH + [host, command], timeout=timeout)


def src_stats(host, path):
    r = remote(host, f"find {path} -type f -printf '%s\\n' 2>/dev/null | "
                     f"awk '{{n++; t+=$1}} END {{print n+0, t+0}}'")
    try:
        n, b = r.stdout.split()
        return int(n), int(b)
    except Exception:
        return 0, 0


def manifest(host, path, timeout=None):
    """Per-file 'md5  relpath', sorted. Generated independently on each end."""
    r = remote(host, f"cd {path} && find . -type f -print0 | xargs -0 md5sum 2>/dev/null | sort -k2",
               timeout=timeout)
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src-host", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--dest-host", required=True)
    ap.add_argument("--dest-user", default=None)
    ap.add_argument("--dest-path", required=True, help="destination PARENT directory")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--index", default=INDEX_DEFAULT)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep-source", action="store_true",
                    help="archive and record, but do not delete (use when unsure)")
    ap.add_argument("--delete-only", action="store_true",
                    help="record and DELETE without archiving -- for data superseded by "
                         "something strictly better, where a copy has no value. Recorded in "
                         "the same index as moves: 'deleted outright, and why' must be as "
                         "answerable later as 'moved, and where to'.")
    ap.add_argument("--direct", action="store_true",
                    help="source host can reach dest directly -- use rsync from there "
                         "instead of relaying tar through this machine. Measured 38 MB/s "
                         "direct (bluehive->repovibranium) vs ~10 MB/s relayed, and rsync "
                         "resumes where a tar stream cannot.")
    a = ap.parse_args()

    src = a.src.rstrip("/")
    name = os.path.basename(src)
    parent = os.path.dirname(src)
    dest_dir = os.path.join(a.dest_path, name)
    dhost = f"{a.dest_user}@{a.dest_host}" if a.dest_user else a.dest_host

    n_files, n_bytes = src_stats(a.src_host, src)
    if a.delete_only:
        print(f"[delete] {a.src_host}:{src}")
        print(f"[delete]   {n_files:,} files, {n_bytes/1e9:.2f} GB   reason: {a.reason}")
        if not a.apply:
            print("[delete] DRY RUN -- nothing deleted.")
            return
        rec = dict(name=name, source_host=a.src_host, source_path=src,
                   dest_host=None, dest_path=None, n_files=n_files, n_bytes=n_bytes,
                   gb=round(n_bytes / 1e9, 3), reason=a.reason, action="deleted-not-archived",
                   moved_at=datetime.datetime.now().isoformat(timespec="seconds"),
                   verified="n/a -- superseded data, deliberately not archived",
                   source_deleted=True, retrieve="NOT RETRIEVABLE -- deleted by design")
        idx = {"moves": []}
        if os.path.exists(a.index):
            try:
                idx = json.load(open(a.index))
            except (ValueError, OSError):
                pass
        idx["moves"] = [m for m in idx.get("moves", [])
                        if not (m.get("source_host") == a.src_host
                                and m.get("source_path") == src)] + [rec]
        tmp = a.index + ".tmp"
        with open(tmp, "w") as f:
            json.dump(idx, f, indent=2)
        os.replace(tmp, a.index)
        tomb = json.dumps(rec, indent=2).replace("'", "'\\''")
        remote(a.src_host, f"mkdir -p {src}.MOVED && printf '%s' '{tomb}' > {src}.MOVED/MOVED.json")
        d = remote(a.src_host, f"rm -rf {src}")
        if d.returncode != 0:
            sys.exit(f"[delete] failed: {d.stderr[-300:]}")
        print(f"[delete] DONE -- freed {n_bytes/1e9:.2f} GB; recorded in {a.index}")
        return
    if n_files == 0:
        sys.exit(f"[archive] nothing found at {a.src_host}:{src}")
    print(f"[archive] {a.src_host}:{src}")
    print(f"[archive]   -> {a.dest_host}:{dest_dir}")
    print(f"[archive]   {n_files:,} files, {n_bytes/1e9:.2f} GB   reason: {a.reason}")
    if not a.apply:
        print("[archive] DRY RUN -- nothing transferred, nothing deleted.")
        return

    print("[archive] 1/4 transferring ...", flush=True)
    # Create the destination FROM THE HOST THAT WILL WRITE IT. In direct mode that is the
    # source host; doing it from here instead silently fails, because this machine's ssh
    # config aliases do not apply to a fully-qualified destination hostname.
    mk = (remote(a.src_host, f"ssh -o BatchMode=yes {dhost} 'mkdir -p {dest_dir}'")
          if a.direct else remote(dhost, f"mkdir -p {dest_dir}"))
    if mk.returncode != 0:
        sys.exit(f"[archive] could not create destination {dest_dir}: {mk.stderr[-300:]}")
    if a.direct:
        r = remote(a.src_host,
                   f"rsync -a --partial {src}/ {dhost}:{dest_dir}/", timeout=None)
        if r.returncode != 0:
            sys.exit(f"[archive] TRANSFER FAILED (rc={r.returncode}); nothing deleted.\n"
                     f"{r.stderr[-600:]}")
        print("[archive]   (direct rsync from source host)")
    else:
        p1 = subprocess.Popen(SSH + [a.src_host, f"tar cf - -C {parent} {name}"],
                              stdout=subprocess.PIPE)
        p2 = subprocess.Popen(SSH + [dhost, f"tar xf - -C {a.dest_path}"], stdin=p1.stdout)
        p1.stdout.close()
        p2.communicate()
        if p2.returncode != 0 or p1.wait() != 0:
            sys.exit(f"[archive] TRANSFER FAILED (src rc={p1.returncode}, "
                     f"dest rc={p2.returncode}); nothing deleted.")

    print("[archive] 2/4 verifying by independent md5 manifests on both ends ...", flush=True)
    m_src = manifest(a.src_host, src)
    m_dst = manifest(dhost, dest_dir)
    if not m_src or not m_dst:
        sys.exit("[archive] could not build manifests; nothing deleted.")
    if m_src != m_dst:
        s_lines, d_lines = set(m_src.splitlines()), set(m_dst.splitlines())
        only_src, only_dst = s_lines - d_lines, d_lines - s_lines
        print(f"[archive] VERIFY FAILED -- {len(only_src)} file(s) differ or missing at dest. "
              f"NOTHING deleted.", file=sys.stderr)
        for l in list(only_src)[:10]:
            print("   src: " + l, file=sys.stderr)
        sys.exit(1)
    n_verified = len(m_src.splitlines())
    print(f"[archive]   verified: {n_verified:,} files, md5 identical on both ends")

    record = dict(
        name=name, source_host=a.src_host, source_path=src,
        dest_host=a.dest_host, dest_user=a.dest_user, dest_path=dest_dir,
        n_files=n_files, n_bytes=n_bytes, gb=round(n_bytes / 1e9, 3),
        reason=a.reason, moved_at=datetime.datetime.now().isoformat(timespec="seconds"),
        verified=f"independent md5 manifests on both ends, {n_verified} files identical",
        source_deleted=not a.keep_source,
        retrieve=f"ssh {dhost} 'tar cf - -C {a.dest_path} {name}' | tar xf - -C <local-parent>")

    print("[archive] 3/4 recording ...", flush=True)
    index = {"moves": []}
    if os.path.exists(a.index):
        try:
            index = json.load(open(a.index))
        except (ValueError, OSError):
            pass
    index["moves"] = [m for m in index.get("moves", [])
                      if not (m.get("source_host") == a.src_host and m.get("source_path") == src)]
    index["moves"].append(record)
    tmp = a.index + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, a.index)
    # tombstone beside the source, so the old location explains itself
    tomb = json.dumps(record, indent=2).replace("'", "'\\''")
    remote(a.src_host, f"mkdir -p {src}.MOVED && printf '%s' '{tomb}' > {src}.MOVED/MOVED.json")
    print(f"[archive]   index: {a.index}")
    print(f"[archive]   tombstone: {a.src_host}:{src}.MOVED/MOVED.json")

    if a.keep_source:
        print("[archive] 4/4 --keep-source: source left in place.")
        return
    print("[archive] 4/4 deleting source (verified copy exists) ...", flush=True)
    d = remote(a.src_host, f"rm -rf {src}")
    if d.returncode != 0:
        sys.exit(f"[archive] delete failed: {d.stderr[-300:]}")
    print(f"[archive] DONE -- freed {n_bytes/1e9:.2f} GB on {a.src_host}")
    print(f"[archive] retrieve: {record['retrieve']}")


if __name__ == "__main__":
    main()
