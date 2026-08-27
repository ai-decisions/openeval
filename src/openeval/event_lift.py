"""Event-level detection lift against the honest per-event null.

One command reproduces both outputs byte-for-byte (no timestamps inside):

    python -m openeval.event_lift --replay replay.json \
        --out-json lift.json --out-md lift.md

Why the naive number overstates: dividing an EVENT-level detection rate
by an ADDRESS-level alert budget mismatches units. An event carrying m
scoreable addresses counts as "detected" when >=1 is flagged, so under a
null that flags a share p of addresses uniformly at random the per-event
null probability is 1-(1-p)^m, not p. This module computes that
event-level null from the recorded per-event address counts and reports
the honest lift next to the naive one.

The replay is read in the openeval.leadtime output shape. Before
computing anything, the module re-derives the replay's own summary counts
from its event rows and refuses to run on an artefact that does not
reproduce itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from statistics import median
from typing import Any

# Scraped-only subset per the replay artefact's own summary convention:
# publication_source is a genuine scrape, not an event-date proxy.
SCRAPED_SOURCES = {"scrape", "scraped"}

EVM_CHAINS = ("ethereum", "tron")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def group_stats(events: list[dict[str, Any]], p: float) -> dict[str, Any]:
    n = len(events)
    detected = sum(1 for e in events if int(e["n_flagged_fin"]) >= 1)
    m_in_graph = [int(e["n_in_graph"]) for e in events]
    m_all = [int(e["n_addresses"]) for e in events]
    null_ig = sum(1.0 - (1.0 - p) ** m for m in m_in_graph)
    null_all = sum(1.0 - (1.0 - p) ** m for m in m_all)
    rate = detected / n
    return {
        "n_events": n,
        "n_detected": detected,
        "detection_rate": round(rate, 4),
        "m_in_graph": {
            "min": min(m_in_graph),
            "median": float(median(m_in_graph)),
            "max": max(m_in_graph),
            "sum": sum(m_in_graph),
            "events_with_m_gt_1": sum(1 for m in m_in_graph if m > 1),
        },
        "m_all_addresses": {
            "min": min(m_all),
            "median": float(median(m_all)),
            "max": max(m_all),
            "sum": sum(m_all),
        },
        "null_event_rate_in_graph_m": round(null_ig / n, 6),
        "null_expected_detections_in_graph_m": round(null_ig, 4),
        "lift_event_level_in_graph_m": round(rate / (null_ig / n), 1),
        "null_event_rate_all_addr_m": round(null_all / n, 6),
        "lift_event_level_all_addr_m": round(rate / (null_all / n), 1),
        "lift_naive_address_level": round(rate / p, 0),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", required=True, type=Path)
    ap.add_argument("--out-json", required=True, type=Path)
    ap.add_argument("--out-md", required=True, type=Path)
    args = ap.parse_args(argv)

    if not args.replay.is_file():
        sys.exit(f"FATAL: artifact missing: {args.replay}")
    meta = {"path": str(args.replay), "bytes": args.replay.stat().st_size,
            "sha256": sha256_of(args.replay)}
    data = json.loads(args.replay.read_text())
    p = float(data["summary"]["fpr"])

    evm = [e for e in data["events"] if e["chain"] in EVM_CHAINS]
    scraped = [e for e in evm if e["publication_source"] in SCRAPED_SOURCES]

    # Fail closed if the artifact no longer matches the summary it carries.
    # Note the scraped-only comparison: `scraped` is EVM-only while the
    # summary's scraped_only_fin spans all chains — so this check also
    # enforces the artefact convention that non-EVM events carry proxy
    # publication dates. A replay violating that convention fails here
    # instead of silently producing a mismatched subset.
    s = data["summary"]
    checks = [
        (len(evm), s["per_chain_fin"]["ethereum"]["n_events"] + s["per_chain_fin"]["tron"]["n_events"]),
        (sum(1 for e in evm if int(e["n_flagged_fin"]) >= 1),
         s["per_chain_fin"]["ethereum"]["n_detected"] + s["per_chain_fin"]["tron"]["n_detected"]),
        (len(scraped), s["scraped_only_fin"]["n_events"]),
        (sum(1 for e in scraped if int(e["n_flagged_fin"]) >= 1), s["scraped_only_fin"]["n_detected"]),
    ]
    for got, want in checks:
        if got != want:
            sys.exit(f"FATAL: events do not reproduce artifact summary: got {got}, want {want}")

    groups = {
        "evm_total": group_stats(evm, p),
        "ethereum": group_stats([e for e in evm if e["chain"] == "ethereum"], p),
        "tron": group_stats([e for e in evm if e["chain"] == "tron"], p),
        "scraped_only": group_stats(scraped, p),
    }

    out = {
        "producer": "openeval.event_lift",
        "source_artifact": meta,
        "fpr_budget_address_level": p,
        "null_model": "flag each scoreable address independently at rate p; "
                      "event detected iff >=1 flagged => P(null detect) = 1-(1-p)^m",
        "aggregation_rule_observed": "event detected iff n_flagged_fin >= 1",
        "groups": groups,
    }
    args.out_json.write_text(json.dumps(out, indent=1) + "\n")

    labels = {
        "evm_total": "EVM total (eth+tron)",
        "ethereum": "Ethereum",
        "tron": "Tron",
        "scraped_only": "Scraped publication dates only",
    }
    lines: list[str] = []
    lines.append("# Event-level lift against the per-event null")
    lines.append("")
    lines.append("**Producer:** `python -m openeval.event_lift` — one command, byte-reproducible.")
    lines.append(f"**Source artifact:** `{meta['path']}` ({meta['bytes']:,} B, sha256 `{meta['sha256']}`)")
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append("| Group | Events | Detected | Rate | m in-graph (min/med/max) | Null event rate | **Lift (event-level)** | Lift (naive, address-level) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for key, label in labels.items():
        g = groups[key]
        mig = g["m_in_graph"]
        lines.append(
            f"| {label} | {g['n_events']} | {g['n_detected']} | {g['detection_rate']*100:.1f} % "
            f"| {mig['min']} / {mig['median']:g} / {mig['max']} "
            f"| {g['null_event_rate_in_graph_m']:.6f} "
            f"| **x{g['lift_event_level_in_graph_m']:g}** "
            f"| x{g['lift_naive_address_level']:g} |"
        )
    lines.append("")
    lines.append("## Method, stated so it cannot be misread")
    lines.append("")
    lines.append(f"- Address-level alert budget p = {p} — the same flagging convention as the")
    lines.append("  replay's own summary (`summary.flagging.evm`).")
    lines.append("- Aggregation rule observed in the replay: event detected iff `n_flagged_fin >= 1`.")
    lines.append("- Null model: each scoreable address flagged independently at rate p; per-event null")
    lines.append("  probability 1-(1-p)^m; group null rate = mean over its events; lift = observed")
    lines.append("  detection rate / null rate.")
    lines.append("- m = `n_in_graph` (addresses that can physically receive a score; an address outside")
    lines.append("  the graph cannot be flagged by the system nor by the random-flagging null). The")
    lines.append("  variant m = `n_addresses` (all registry addresses) is also computed in the JSON.")
    lines.append("- The naive column divides the event-level rate by the address-level budget p; it is")
    lines.append("  reported only to make the unit mismatch visible.")
    lines.append("- Scraped-only subset = `publication_source` in {scrape, scraped} — reproduces the")
    lines.append("  artifact's own `summary.scraped_only_fin` composition exactly (checked, fail-closed).")
    lines.append("- Independence caveat: addresses of one event are structurally correlated; correlation")
    lines.append("  under the null could only make the null rate SMALLER (flags clustering on fewer")
    lines.append("  events), i.e. the reported lift is conservative in direction relative to a")
    lines.append("  positively-correlated null.")
    lines.append("")
    args.out_md.write_text("\n".join(lines))
    print(f"OK: wrote {args.out_md} and {args.out_json}")


if __name__ == "__main__":
    main()
