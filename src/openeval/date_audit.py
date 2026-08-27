"""Patch proxy publication dates with audited real dates and recompute leads.

A replay built before a date audit may carry proxy publication dates for
events whose source page gave none — marked by a sentinel `event_date`.
This producer reads the ORIGINAL replay artefact (which it never touches)
plus a patch file, replaces the publication date of every sentinel-dated
event that the audit resolved, and emits a NEW artefact.

Only publication dates change: detection (flagging) is date-independent,
so per-event detected/flagged counts are carried unchanged; leads and the
summary (medians, positive-lead counts, scraped-only subset) are
recomputed. Events whose patch row is unresolved keep their old proxy
date and are counted in the output.

Inputs:
  --replay   the original replay JSON (openeval.leadtime output shape)
  --patch    {"<event name>": {"date": "YYYY-MM-DD" | "UNRESOLVED",
                               "scope": str, "source": URL}, ...}
Output:
  --out-json / --out-md   the corrected artefact (+ report with its sha)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from openeval.leadtime import parse_ts

# Sources that count as a genuine publication date rather than a proxy.
# "<patched-source>" is appended at run time.
SCRAPED_SOURCES = ("scraped", "scrape")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", required=True, type=Path)
    ap.add_argument("--patch", required=True, type=Path)
    ap.add_argument("--out-json", required=True, type=Path)
    ap.add_argument("--out-md", required=True, type=Path)
    ap.add_argument("--sentinel-date", default="2020-01-01",
                    help="event_date value marking a default-dated event eligible for the patch")
    ap.add_argument("--patched-source", default="ofac_scraped_d7",
                    help="publication_source label written on patched events; counted as scraped")
    ap.add_argument("--utc-stamp", default=None,
                    help="fixed UTC stamp for reproducible output (default: now)")
    args = ap.parse_args(argv)

    orig = json.loads(args.replay.read_text())
    patch = json.loads(args.patch.read_text())
    scraped_sources = (*SCRAPED_SOURCES, args.patched_source)

    n_candidates = sum(1 for e in orig["events"] if e["event_date"] == args.sentinel_date)
    rows = []
    n_patched = 0
    unresolved = []
    for e in orig["events"]:
        e = dict(e)
        p = patch.get(e["name"])
        if p and e["event_date"] == args.sentinel_date:
            if p.get("date") and p["date"] != "UNRESOLVED":
                e["publication_date"] = p["date"]
                e["publication_source"] = args.patched_source
                e["audited_source_url"] = p.get("source", "")
                e["audited_scope"] = p.get("scope", "")
                n_patched += 1
            else:
                unresolved.append(e["name"])
        # recompute leads from the (possibly new) publication date
        pub = datetime.fromisoformat(e["publication_date"]).replace(tzinfo=UTC)
        det = e.get("detection_time_fin")
        e["lead_days_fin"] = (round((pub - parse_ts(det)).total_seconds() / 86400.0, 2)
                              if det else None)
        # either-head lead: earliest flagged_either first_seen from addresses
        fs_either = sorted(a["first_seen"] for a in e["addresses"]
                           if a.get("flagged_either") and a.get("first_seen"))
        e["lead_days_either"] = (round(
            (pub - parse_ts(fs_either[0])).total_seconds() / 86400.0, 2)
            if fs_either else None)
        rows.append(e)

    def agg(chain: str | None, key: str, scraped_only: bool = False) -> dict:
        sel = [r for r in rows
               if (chain is None or r["chain"] == chain) and r[key] is not None
               and (not scraped_only or r["publication_source"] in scraped_sources)]
        tot = [r for r in rows
               if (chain is None or r["chain"] == chain)
               and (not scraped_only or r["publication_source"] in scraped_sources)]
        leads = [r[key] for r in sel]
        return {"n_events": len(tot), "n_detected": len(sel),
                "median_lead_days": round(median(leads), 1) if leads else None,
                "n_positive_lead": sum(1 for x in leads if x > 0)}

    summary = {
        "fpr": orig["summary"]["fpr"],
        "flagging": orig["summary"]["flagging"],
        "date_audit": {"patched_events": n_patched, "unresolved": unresolved,
                       "patch_file": str(args.patch)},
        "per_chain_fin": {c: agg(c, "lead_days_fin")
                          for c in ("ethereum", "tron", "bitcoin")},
        "per_chain_either": {c: agg(c, "lead_days_either")
                             for c in ("ethereum", "tron", "bitcoin")},
        "all_fin": agg(None, "lead_days_fin"),
        "all_either": agg(None, "lead_days_either"),
        "scraped_only_fin": agg(None, "lead_days_fin", scraped_only=True),
    }
    out = {
        "utc": args.utc_stamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "producer": "openeval.date_audit",
        "inputs": {"original": str(args.replay),
                   "original_sha256": hashlib.sha256(args.replay.read_bytes()).hexdigest(),
                   "patch": str(args.patch),
                   "patch_sha256": hashlib.sha256(args.patch.read_bytes()).hexdigest()},
        "summary": summary,
        "events": rows,
    }
    args.out_json.write_text(json.dumps(out, indent=1))
    sha = hashlib.sha256(args.out_json.read_bytes()).hexdigest()

    md = [
        "# Lead-time replay with audited publication dates",
        "",
        f"**JSON:** `{args.out_json}` sha256 `{sha}`",
        f"**Patched:** {n_patched} of {n_candidates} default-dated events; "
        f"unresolved: {unresolved or 'none'}",
        "",
        "| Group | Events | Detected | Median lead, days | Positive lead |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, a in (("ethereum", summary["per_chain_fin"]["ethereum"]),
                     ("tron", summary["per_chain_fin"]["tron"]),
                     ("ALL", summary["all_fin"]),
                     ("scraped-pub only (incl. audited)", summary["scraped_only_fin"])):
        md.append(f"| {label} | {a['n_events']} | {a['n_detected']} | "
                  f"{a['median_lead_days'] if a['median_lead_days'] is not None else '—'} | "
                  f"{a['n_positive_lead']} |")
    args.out_md.write_text("\n".join(md) + "\n")
    print(f"patched {n_patched} of {n_candidates}, unresolved {len(unresolved)}: {unresolved}")
    for label in ("ethereum", "tron"):
        print(label, summary["per_chain_fin"][label])
    print("scraped_only_fin", summary["scraped_only_fin"])


if __name__ == "__main__":
    main()
