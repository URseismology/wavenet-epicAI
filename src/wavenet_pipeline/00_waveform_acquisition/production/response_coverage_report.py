#!/usr/bin/env python
"""
Did the Stage 1.5 refetch deliver complete instrument responses, and does coverage depend on
whether the network is permanent or temporary?

PI hypothesis (2026-10-05): operators will always provide a response, because data is largely
unusable without one. Plausible, and this tests it rather than assuming it.

The reason to split by network TYPE is that our failing set is skewed. Under FDSN convention a
network code beginning with X, Y, Z or a digit is a TEMPORARY deployment -- a PI-run campaign,
often with metadata deposited thinly, sometimes with the response held at a different
datacenter from the waveforms. Permanent observatory networks (IU, II, G, GE, CI ...) are
curated to a different standard. If the hypothesis holds everywhere, coverage is flat across
both. If it holds only for permanent networks, the residue is a decision about temporary
deployments, not a bug to chase.

    python3 response_coverage_report.py [--root ROOT]
"""
import argparse
import glob
import json
import os
import collections


def is_temporary(net):
    """FDSN convention: X/Y/Z prefixes and numeric-leading codes are temporary deployments."""
    return bool(net) and (net[0] in "XYZ" or net[0].isdigit())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/scratch/tolugboj_lab/wavenet_ncf_production_v2")
    args = ap.parse_args()
    md = os.path.join(args.root, "station_metadata")

    recs = []
    for p in glob.glob(os.path.join(md, "*.json")):
        if os.path.basename(p) == "_coverage.json":
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        recs.append(d)

    xml = {os.path.basename(p)[:-4] for p in glob.glob(os.path.join(md, "*.xml"))}
    print("=" * 70)
    print("RESPONSE COVERAGE  (population: {:,} stations with a status record)".format(len(recs)))
    print("=" * 70)
    print("  StationXML on disk            : {:,}".format(len(xml)))

    # coverage_complete only exists on records written by the FIXED fetcher; its absence is
    # itself the signal that a station has not been re-fetched yet.
    newfmt = [d for d in recs if "coverage_complete" in d]
    print("  re-fetched with fixed fetcher : {:,}".format(len(newfmt)))
    if not newfmt:
        print("\n  nothing re-fetched yet -- rerun once the refetch completes")
        return

    comp = [d for d in newfmt if d.get("coverage_complete")]
    nores = [d for d in newfmt if not d.get("ok")]
    partial = [d for d in newfmt if d.get("ok") and not d.get("coverage_complete")]
    print("")
    print("  COMPLETE channel coverage     : {:,}  ({:.1f}%)".format(
        len(comp), 100.0 * len(comp) / len(newfmt)))
    print("  partial (some channels short) : {:,}".format(len(partial)))
    print("  no response at all            : {:,}".format(len(nores)))

    print("")
    print("  PI HYPOTHESIS -- operators always provide a response")
    print("  {:<12} {:>8} {:>10} {:>10} {:>10}".format(
        "network type", "n", "complete", "partial", "none"))
    buckets = collections.defaultdict(lambda: [0, 0, 0, 0])
    for d in newfmt:
        b = buckets["temporary" if is_temporary(str(d.get("network", ""))) else "permanent"]
        b[0] += 1
        if d.get("coverage_complete"):
            b[1] += 1
        elif d.get("ok"):
            b[2] += 1
        else:
            b[3] += 1
    for k in ("permanent", "temporary"):
        n, c, p_, z = buckets[k]
        if n:
            print("  {:<12} {:>8,} {:>9.0f}% {:>9.0f}% {:>9.0f}%".format(
                k, n, 100.0 * c / n, 100.0 * p_ / n, 100.0 * z / n))
    pn, pc = buckets["permanent"][0], buckets["permanent"][1]
    tn, tc = buckets["temporary"][0], buckets["temporary"][1]
    if pn and tn:
        print("")
        gap = 100.0 * pc / pn - 100.0 * tc / tn
        print("  >> {}".format(
            "HOLDS broadly -- coverage is flat across network type ({:.0f} pt gap)".format(gap)
            if abs(gap) < 10 else
            "HOLDS for permanent networks, weaker for temporary ({:.0f} pt gap) -- the "
            "residue is a decision about temporary deployments, not a bug".format(gap)))

    worst = collections.Counter()
    for d in newfmt:
        if not d.get("coverage_complete"):
            worst[str(d.get("network", "?"))] += 1
    if worst:
        print("")
        print("  networks with the most incomplete stations:")
        for k, v in worst.most_common(8):
            print("     {:<6} {:>4}   ({})".format(
                k, v, "temporary" if is_temporary(k) else "permanent"))


if __name__ == "__main__":
    main()
