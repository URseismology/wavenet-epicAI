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
