#!/usr/bin/env python
"""
Single-instance PID lock for logger.py/inspector.py. Both are meant to be exactly ONE
live process per root at a time (logger is the only writer to the master HDF5; inspector
is the only thing that purges raw SEED) -- two live copies against the same root race on
their own in-memory `verified_idx`/`merged_idx` sets and can double-process the same
station (confirmed directly, 2026-09-23: a leftover inspector from an earlier failed test
that was never explicitly `scancel`'d kept polling the same root path after it was wiped
and reused, producing duplicate verify/purge log entries once new results appeared).
"""
import os
import time

import h5py


def open_h5_retry(path, mode, max_attempts=8, base_delay=1.0):
    """h5py.File open with retry on a locked file, for the shared master HDF5 that
    logger.py (the sole writer) and inspector.py (the sole reader) both touch. HDF5's
    default file locking blocks a second opener (read OR write) while either side has
    the file open -- a real, recurring race between two independent 30s-polling
    processes, not a hypothetical: confirmed in production 2026-09-24, where inspector
    hit `BlockingIOError: unable to lock file` on essentially its first verification
    attempt (logger was mid-merge at that instant) and, with no retry, crashed its
    entire poll loop over and over until inspector.slurm's process-level retry wrapper
    gave up after 4 attempts -- 0 stations ever verified/purged. logger's own merge
    window is brief (one station's channels), so a short retry-with-backoff here
    resolves the race without either process needing to coordinate directly."""
    last_err = None
    for attempt in range(max_attempts):
        try:
            return h5py.File(path, mode)
        except (BlockingIOError, OSError) as e:
            last_err = e
            time.sleep(base_delay * (attempt + 1))
    raise last_err


def acquire_singleton_lock(lock_path):
    """Raises SystemExit if another live process already holds this lock. Otherwise
    writes this process's PID and returns. An orphaned lock from a genuinely-dead
    process (PID no longer exists) is detected and reclaimed automatically."""
    if os.path.exists(lock_path):
        with open(lock_path) as f:
            old_pid = f.read().strip()
        if old_pid.isdigit() and _pid_alive(int(old_pid)):
            raise SystemExit(
                f"Refusing to start: {lock_path} is held by live PID {old_pid}. "
                f"If that's a stale/orphaned job from a previous test, `scancel` it "
                f"first, don't just delete this lock file out from under it."
            )
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else -- still alive
    return True


def scratch_quota(path="/scratch/tolugboj_lab", timeout=30):
    """Scratch usage vs the quota that actually GOVERNS `path`, or None if unavailable.

    Two traps here, both of which gave a falsely reassuring answer at first:

    1. `df` reports hundreds of TB free on the shared filesystem. That is capacity, not
       entitlement -- the quota is the real ceiling.
    2. Bare `circ-quota` reports the USER's quota (506 GB of 11 TB used). But everything
       this pipeline writes lives in a setgid group directory and is charged to the GROUP,
       which has a separate and far more relevant quota: 89.5 TB of 102 TB used, i.e. 86%
       consumed with ~15 TB free. Reading the user's number instead of the group's
       understates usage by two orders of magnitude.

    So: resolve the group owning `path` and read that group's report. CIRC precomputes
    these per-id under /software/circ/share/quota-rep (refreshed daily), so this is a cheap
    file read rather than a filesystem walk.
    """
    import os as _os
    try:
        gid = _os.stat(path).st_gid
    except OSError:
        return None
    report = f"/software/circ/share/quota-rep/{gid}"
    try:
        with open(report) as f:
            out = f.read()
    except OSError:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "/scratch":
            try:
                used, soft, hard = float(parts[1]), float(parts[2]), float(parts[3])
            except ValueError:
                return None
            return dict(used_gb=used, soft_gb=soft, hard_gb=hard, gid=gid,
                         pct_of_hard=(100.0 * used / hard) if hard else 0.0,
                         free_gb=hard - used)
    return None
