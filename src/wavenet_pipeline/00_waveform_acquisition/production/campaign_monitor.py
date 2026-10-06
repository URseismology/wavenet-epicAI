#!/usr/bin/env python3
"""Diagnostic monitor for the NCF campaign -- v1 and v2, whichever root is pointed at.

Not alert-only: every invocation composes a FULL report (goal-tracking numbers +
any issues found), because a report that only lists problems in isolation, with no
context for what's actually going well, is hard to act on. Delivery still defaults to
"only actually send when something's wrong" (the original ask), but the full report is
always what gets composed and shown -- --digest forces sending it regardless.

Root is a parameter, not hardcoded, specifically so this works unchanged against v1
(`wavenet_ncf_production`, the superseded run) as well as v2 -- useful for comparing the
two, or for anyone who still needs to check on v1's final state.

Delivery: relayed through terravibranium's mail command over SSH, one invocation per
recipient. See the long comment lower in this file for why -- short version: BH3's own
sendmail doesn't actually deliver (no running local mail queue) and SLURM's own
--mail-type notification can't carry custom content no matter what job-level field is
attached, both confirmed by direct test 2026-09-30. terravibranium's Postfix is
genuinely active (it already sends real RAID-monitoring alerts) and was confirmed to
deliver real multi-line content end to end.

PROVISIONING and SHARD sections (PI, 2026-09-30) added after the first report landed:
  - Provisioning: number of jobs currently running/pending and which partitions they're
    on (get_provisioning()) -- not storage quota share, that was an earlier, incorrect
    guess at what "provisioning size" meant, corrected by the PI directly. Same
    whole-account scope caveat as orchestrator_tasks: squeue has no per-root concept.
  - Shard count and summary (get_shard_stats()): a direct find/du against packaged_h5/
    on disk, reported alongside master.py's own self-reported packaged_gb as an
    independent ground-truth cross-check -- the self-reported number could drift from
    what's actually on disk, the same way "success flag" and "actual data obtained"
    diverged earlier in this project (see [[feedback_measure_against_ground_truth]]).
"""
import json, os, re, subprocess, sys, time

DEFAULT_ROOT = "/scratch/tolugboj_lab/wavenet_ncf_production_v2"
# Resolve master.py through the stable CURRENT symlink, never a physical tree. The literal
# path here used to be `.../wavenet_ncf_framework/production/master.py`, which the 2026-10-05
# consolidation archived -- so the monitor would have reported on the campaign by shelling
# out to a master.py that no longer existed. Same failure shape as the incident it is
# monitoring for: a path baked once, pointing at a tree that moved underneath it.
_CANONICAL = "/scratch/tolugboj_lab/wavenet_ncf/code/CURRENT/production/master.py"
_LEGACY = "/scratch/tolugboj_lab/wavenet_ncf_framework/production/master.py"
FRAMEWORK = os.environ.get("WAVENET_MASTER") or (
    _CANONICAL if os.path.exists(_CANONICAL) else _LEGACY)
STATE_DIR = os.path.expanduser("~/.wavenet_monitor")
SCRATCH_PCT_ALERT = float(os.environ.get("MONITOR_SCRATCH_PCT_ALERT", "90.0"))
HOME_PCT_ALERT = float(os.environ.get("MONITOR_HOME_PCT_ALERT", "95.0"))
STALL_HOURS = float(os.environ.get("MONITOR_STALL_HOURS", "6"))

# Anchors every report back to what the campaign is actually FOR, not just infra health
# -- see docs/ncf_pipeline_stages/DATA_AVAILABILITY_AND_COST_REPORT.md and the
# 2026-09-30 memo for the full derivation of these numbers.
DESIGN_GOAL = (
    "2,000-station farthest-point-sampled global network, ray-path coverage density "
    "as the design objective (not station/pair count). Full history: 78.9 TB raw, "
    "~5.0 TB packaged design estimate."
)


def get_root():
    for a in sys.argv:
        if a.startswith("--root="):
            return a.split("=", 1)[1]
    if "--root" in sys.argv:
        return sys.argv[sys.argv.index("--root") + 1]
    return DEFAULT_ROOT


ROOT = get_root()
# PATH is set directly here, NOT via `module load`: master.py's own progress command
# shells out to `squeue` directly, and `module load` -- confirmed by direct test,
# 2026-09-30 -- does not reliably work inside a `bash -lc` spawned from a nested Python
# subprocess the way it does in a top-level `ssh host "..."` shell; the module system's
# own init script (`export: _module_raw: not a function`) breaks on repeated sourcing
# in that context. Prepending the known absolute bin path sidesteps that fragile
# machinery entirely rather than fighting it further.
SLURM_BIN = "/sfw/rhel9-x86_64/slurm/24.05.0.b1/bin"
PROGRESS_CMD = (
    f"export PATH={SLURM_BIN}:$PATH; "
    "source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/profile.d/"
    f"conda.sh && conda activate instaseis && python3 {FRAMEWORK} progress --root {ROOT}"
)


# The cluster's own module-init script prints this on essentially every single shell
# invocation all session, regardless of whether anything is actually wrong -- a benign
# quirk of this site's module system, not a real error. Flagging it as an "issue" would
# make every future check report "1 issue" even when truly healthy, exactly the
# cry-wolf failure mode already caught once (see the inspector-chain heuristic fix).
_BENIGN_STDERR = "export: _module_raw: not a function"


def run_progress():
    r = subprocess.run(["bash", "-lc", PROGRESS_CMD], capture_output=True, text=True,
                        timeout=120)
    real_err = "\n".join(l for l in r.stderr.splitlines() if _BENIGN_STDERR not in l)
    return r.stdout, real_err


def get_provisioning():
    """Number of jobs and which partitions are actually in use right now -- PI,
    2026-09-30. Deliberately whole-account (squeue -u tolugboj), same scope caveat as
    master.py's own orchestrator-task count: this is every job under the account, not
    scoped to one campaign root, since BH3's queue itself has no per-root concept (see
    disambiguation #0.4 in the 2026-09-30 handoff doc)."""
    cmd = (f"export PATH={SLURM_BIN}:$PATH; squeue -u tolugboj -h -o '%P %T'")
    r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=30)
    counts = {}
    total = 0
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        part, state = parts
        counts.setdefault(part, {}).setdefault(state, 0)
        counts[part][state] += 1
        total += 1
    return total, counts


def get_shard_stats(root):
    """Direct count/size of what's actually ON DISK in packaged_h5/, independent of
    master.py's own self-reported downloaded/packaged GB estimate -- a ground-truth
    cross-check, not a replacement for it (see the docstring's FUTURE ENHANCEMENTS note
    this implements)."""
    h5_dir = os.path.join(root, "packaged_h5")
    r = subprocess.run(["bash", "-lc",
                        f"find {h5_dir} -maxdepth 1 -name '*.h5' | wc -l"],
                       capture_output=True, text=True, timeout=60)
    try:
        n_shards = int(r.stdout.strip())
    except ValueError:
        n_shards = None
    r = subprocess.run(["bash", "-lc", f"du -sb {h5_dir} 2>/dev/null | cut -f1"],
                       capture_output=True, text=True, timeout=120)
    try:
        bytes_on_disk = int(r.stdout.strip())
    except ValueError:
        bytes_on_disk = None
    return n_shards, bytes_on_disk


def parse_progress(out):
    """Pulls every field master.py's progress reporter prints, not just the ones an
    earlier, narrower version of this script checked -- full state, not a health-check
    subset, so a report is actually useful for debugging and for tracking real progress
    against the campaign's goals, not just infra uptime."""
    d = {}

    def grab(pattern, key, cast=str):
        m = re.search(pattern, out)
        if m:
            d[key] = cast(m[1].replace(",", "")) if cast in (int, float) else m[1]

    grab(r"(\d+)/(\d+) stations reported \(([\d.]+)%\)", "_reported_block")
    m = re.search(r"(\d+)/(\d+) stations reported \(([\d.]+)%\)", out)
    if m:
        d["stations_reported"], d["stations_total"] = int(m[1]), int(m[2])
        d["pct_reported"] = float(m[3])
    grab(r"with data\s*:\s*(\d+)\s*\(patched (\d+) / pre-patch (\d+)\)", None)
    m = re.search(r"with data\s*:\s*(\d+)\s*\(patched (\d+) / pre-patch (\d+)\)", out)
    if m:
        d["with_data"], d["patched"], d["pre_patch"] = int(m[1]), int(m[2]), int(m[3])
    m = re.search(r"indexed / purged\s*:\s*(\d+)\s*/\s*(\d+)\s*days refused "
                  r"\(data lost\):\s*([\d,]+)", out)
    if m:
        d["indexed"], d["purged"] = int(m[1]), int(m[2])
        d["days_lost"] = int(m[3].replace(",", ""))
    m = re.search(r"days checkpointed\s*:\s*([\d,]+)\s*/\s*([\d,]+)\s*\(([\d.]+)%\)", out)
    if m:
        d["days_checkpointed"] = int(m[1].replace(",", ""))
        d["days_total"] = int(m[2].replace(",", ""))
        d["days_pct"] = float(m[3])
    m = re.search(r"downloaded\s*:\s*~?([\d.]+)\s*GB.*?packaged:\s*([\d.]+)\s*GB", out)
    if m:
        d["downloaded_gb"], d["packaged_gb"] = float(m[1]), float(m[2])
    m = re.search(r"rate:\s*(.+)", out)
    if m:
        d["rate_line"] = m[1].strip()
    m = re.search(r"home quota \(group\)\s*:\s*([\d.]+)\s*GB used of\s*([\d.]+)\s*GB "
                  r"hard\s*\(([\d.]+)%\)", out)
    if m:
        d["home_pct"] = float(m[3])
    m = re.search(r"scratch quota\s*:\s*([\d,]+)\s*GB used of\s*([\d,]+)\s*GB hard\s*"
                  r"\(([\d.]+)%", out)
    if m:
        d["scratch_used_gb"] = int(m[1].replace(",", ""))
        d["scratch_hard_gb"] = int(m[2].replace(",", ""))
        d["scratch_pct"] = float(m[3])
    m = re.search(r"orchestrator\s*(\d+)\s*task\(s\)\s*\|\s*inspector chain:\s*(.+)", out)
    if m:
        d["orchestrator_tasks"] = int(m[1])
        d["inspector_chain"] = m[2].strip()
    return d


def check():
    out, err = run_progress()
    now = parse_progress(out)
    now["_ts"] = time.time()
    now["_root"] = ROOT
    if err.strip():
        now["_stderr"] = err.strip()[-500:]
    if not now.get("stations_reported") and not err.strip():
        now["_stderr"] = (now.get("_stderr", "") +
                          " | WARNING: progress output did not parse as expected -- "
                          "raw output follows:\n" + out[-800:])

    now["_n_jobs"], now["_partitions"] = get_provisioning()
    now["_n_shards"], now["_shard_bytes"] = get_shard_stats(ROOT)

    state_file = os.path.join(STATE_DIR, re.sub(r"[^a-zA-Z0-9_.-]", "_", ROOT) + ".json")
    os.makedirs(STATE_DIR, exist_ok=True)
    prev = {}
    if os.path.exists(state_file):
        try:
            prev = json.load(open(state_file))
        except (ValueError, OSError):
            pass

    issues = []
    chain = now.get("inspector_chain", "")
    # "OK" covers both steady states of the self-chain design: running-only, or
    # running + already-queued successor (2026-09-30 fix -- see master.py's own
    # comment on this exact heuristic for why "2" is normal, not a duplicate).
    if chain and "OK" not in chain:
        issues.append(
            f"INSPECTOR CHAIN: \"{chain}\" -- the process that frees scratch space "
            f"may have stopped."
        )
    if now.get("scratch_pct", 0) >= SCRATCH_PCT_ALERT:
        issues.append(f"SCRATCH QUOTA: {now['scratch_pct']:.1f}% of hard limit "
                      f"(threshold {SCRATCH_PCT_ALERT:.0f}%).")
    if now.get("home_pct", 0) >= HOME_PCT_ALERT:
        issues.append(f"HOME QUOTA: {now['home_pct']:.1f}% of hard limit -- group may "
                      f"already be unable to write to $HOME.")
    if prev.get("days_checkpointed") is not None and "days_checkpointed" in now:
        elapsed_h = (now["_ts"] - prev["_ts"]) / 3600.0
        if elapsed_h >= STALL_HOURS and now["days_checkpointed"] <= prev["days_checkpointed"]:
            issues.append(f"NO PROGRESS: days checkpointed unchanged "
                          f"({now['days_checkpointed']:,}) across {elapsed_h:.1f}h.")
    if now.get("_stderr"):
        issues.append(f"progress command issue: {now['_stderr']}")

    json.dump(now, open(state_file, "w"))
    return now, issues, prev


def _num(v):
    return f"{v:,}" if isinstance(v, int) else str(v)


def fmt(now, prev):
    days_line = f"  days checkpointed : {_num(now.get('days_checkpointed', '?'))}"
    if isinstance(now.get("days_total"), int):
        days_line += f" of {now['days_total']:,} ({now.get('days_pct', '?')}%)"

    with_data_line = f"  with real data    : {now.get('with_data', '?')}"
    if "patched" in now:
        with_data_line += f" (patched {now['patched']} / pre-patch {now['pre_patch']})"

    lines = [
        f"Campaign root: {now.get('_root')}",
        f"Design goal  : {DESIGN_GOAL}",
        "",
        "PROGRESS (goal-tracking):",
        f"  stations reported : {now.get('stations_reported','?')}/"
        f"{now.get('stations_total','?')} ({now.get('pct_reported','?')}%)",
        with_data_line,
        days_line,
        f"  days lost (refused): {_num(now.get('days_lost', '?'))}",
        f"  downloaded/packaged: ~{now.get('downloaded_gb','?')} GB / "
        f"{now.get('packaged_gb','?')} GB",
        f"  rate/ETA          : {now.get('rate_line','?')}",
        "",
        "INFRASTRUCTURE:",
        f"  scratch quota     : {now.get('scratch_pct','?')}%",
        f"  home quota        : {now.get('home_pct','?')}%",
        f"  orchestrator tasks: {now.get('orchestrator_tasks','?')}",
        f"  inspector chain   : {now.get('inspector_chain','?')}",
        "",
        "PROVISIONING (jobs and partitions actually in use right now):",
        f"  total jobs        : {now.get('_n_jobs', '?')}",
    ]
    parts = now.get("_partitions") or {}
    if parts:
        for part in sorted(parts):
            state_str = ", ".join(f"{n} {s}" for s, n in sorted(parts[part].items()))
            lines.append(f"    {part:<14s}: {state_str}")
    else:
        lines.append("    (none running)")

    lines += [
        "",
        "SHARDS ON DISK (direct count, independent of the self-reported GB above):",
        f"  .h5 files         : {now.get('_n_shards', '?')}",
    ]
    if isinstance(now.get("_shard_bytes"), int):
        measured_gb = now["_shard_bytes"] / 1e9
        lines.append(f"  measured size     : {measured_gb:.2f} GB")
        if isinstance(now.get("packaged_gb"), float):
            delta_pct = (100 * abs(measured_gb - now["packaged_gb"]) /
                        max(measured_gb, now["packaged_gb"], 1e-9))
            lines.append(f"  vs self-reported  : {now['packaged_gb']:.2f} GB "
                        f"({delta_pct:.1f}% difference)")
    return "\n".join(lines)


def compose_report(now, issues, prev):
    status = "HEALTHY" if not issues else f"{len(issues)} ISSUE(S)"
    subj = f"[wavenet-ncf] {os.path.basename(now.get('_root',''))}: {status}"
    body = [f"Automated report, {time.strftime('%Y-%m-%d %H:%M %Z')}", ""]
    if issues:
        body.append("ISSUES:")
        for i in issues:
            body.append(f"  - {i}")
        body.append("")
    body.append(fmt(now, prev))
    body += [
        "",
        "Check directly:",
        "  ssh bluehive3",
        "  source /scratch/tolugboj_lab/softwares/anaconda/anaconda3/2021.05/etc/"
        "profile.d/conda.sh && conda activate instaseis",
        f"  python3 {FRAMEWORK} progress --root {now.get('_root')}",
        "",
        "Full context: docs/ncf_pipeline_stages/PROGRESS.md, "
        "docs/ncf_pipeline_stages/2026-09-30_HANDOFF_pre_pruning.md, "
        "docs/memos/2026-09-30-ncf-v2-campaign-bugs-recovery-and-backup.md",
    ]
    return subj, "\n".join(body)


MAIL_RELAY_HOST = "tolugboj@terravibranium.earth.rochester.edu"
RECIPIENTS = [
    "tolugboj@ur.rochester.edu",
    "cpenagon@u.rochester.edu",
    "tbalamur@ur.rochester.edu",
]


def send_via_terravibranium(subject, body, recipients):
    sent = []
    for addr in recipients:
        r = subprocess.run(["ssh", MAIL_RELAY_HOST, f"mail -s {subject!r} {addr}"],
                            input=body, capture_output=True, text=True, timeout=30)
        sent.append((addr, r.returncode, r.stderr.strip()))
    return sent


if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    test_only = "--test-self-only" in sys.argv
    digest = "--digest" in sys.argv  # send even when healthy

    now, issues, prev = check()
    subj, body = compose_report(now, issues, prev)
    print(f"SUBJECT: {subj}\n\n{body}")

    should_send = issues or digest
    if not should_send:
        print("\n[no issues, not a --digest run -- nothing would be sent]")
        sys.exit(0)
    if dry_run:
        print("\n[DRY RUN -- nothing sent. Pass --apply to actually relay through "
              "terravibranium's mail command.]")
    else:
        targets = RECIPIENTS[:1] if test_only else RECIPIENTS
        results = send_via_terravibranium(subj, body, targets)
        print()
        for addr, rc, err in results:
            print(f"  {addr}: {'sent' if rc == 0 else 'FAILED rc=' + str(rc) + ' ' + err}")
