"""Event-level lift + group stats for a corrected replay, gated on invariants.

A publication-date audit (openeval.date_audit) may not move detection:
flagging and the per-event address counts are date-independent. This
module recomputes the lift groups for the corrected replay and REFUSES to
emit anything if the date-invariant figures (event counts, detections,
null rates) differ from the invariants you pin — catching both a corrupted
artefact and an audit that silently changed more than dates.

One command, byte-for-byte reproducible (no timestamps inside):

    python -m openeval.event_lift_gate --replay corrected.json \
        --invariants invariants.json --out lift.json

Invariants file shape (see examples/invariants_reported.json for the
values reported in the accompanying paper):

    {"ethereum":  {"n_events": .., "n_detected": ..,
                   "null_event_rate_in_graph_m": ..},
     "tron":      {...},
     "evm_total": {...}}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from statistics import median

from openeval.event_lift import EVM_CHAINS, group_stats

# The audited-date label written by openeval.date_audit counts as scraped.
SCRAPED = {"scrape", "scraped", "ofac_scraped_d7"}
NULL_RATE_TOLERANCE = 5e-6


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", required=True, type=Path,
                    help="the corrected replay (openeval.date_audit output)")
    ap.add_argument("--invariants", required=True, type=Path,
                    help="pinned date-invariant figures the gate enforces")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    data = json.loads(args.replay.read_text())
    want = json.loads(args.invariants.read_text())
    p = float(data["summary"]["fpr"])
    evm = [e for e in data["events"] if e["chain"] in EVM_CHAINS]
    scraped = [e for e in evm if e["publication_source"] in SCRAPED]

    groups = {
        "evm_total": group_stats(evm, p),
        "ethereum": group_stats([e for e in evm if e["chain"] == "ethereum"], p),
        "tron": group_stats([e for e in evm if e["chain"] == "tron"], p),
        "scraped_only_incl_audited": group_stats(scraped, p),
    }
    # lead medians per group from the corrected dates
    for key, evs in (("evm_total", evm),
                     ("ethereum", [e for e in evm if e["chain"] == "ethereum"]),
                     ("tron", [e for e in evm if e["chain"] == "tron"]),
                     ("scraped_only_incl_audited", scraped)):
        leads = [e["lead_days_fin"] for e in evs if e["lead_days_fin"] is not None]
        groups[key]["median_lead_days"] = round(median(leads), 1) if leads else None
        groups[key]["n_positive_lead"] = sum(1 for x in leads if x > 0)

    # invariance gate: detection/null/lift for the pinned groups must equal
    # the pinned line (dates do not enter them)
    for g, exp in want.items():
        if g not in groups:
            sys.exit(f"FATAL: invariants pin unknown group {g!r}; "
                     f"known groups: {sorted(groups)}")
        got = groups[g]
        if (got["n_events"], got["n_detected"]) != (exp["n_events"], exp["n_detected"]) or \
                abs(got["null_event_rate_in_graph_m"] - exp["null_event_rate_in_graph_m"]) \
                > NULL_RATE_TOLERANCE:
            sys.exit(f"FATAL: {g} does not reproduce the pinned invariants: {got}")

    out = {
        "producer": "openeval.event_lift_gate",
        "source_artifact": {"path": str(args.replay),
                            "bytes": args.replay.stat().st_size,
                            "sha256": hashlib.sha256(args.replay.read_bytes()).hexdigest()},
        "invariants": {"path": str(args.invariants),
                       "sha256": hashlib.sha256(args.invariants.read_bytes()).hexdigest()},
        "fpr_budget_address_level": p,
        "groups": groups,
    }
    args.out.write_text(json.dumps(out, indent=1))
    for g, gg in groups.items():
        print(g, "| n", gg["n_events"], "det", gg["n_detected"],
              "| null%", round(gg["null_event_rate_in_graph_m"] * 100, 3),
              "| lift", gg["lift_event_level_in_graph_m"],
              "| median", gg["median_lead_days"], "| pos", gg["n_positive_lead"])


if __name__ == "__main__":
    main()
