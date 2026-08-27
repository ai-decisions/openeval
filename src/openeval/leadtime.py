"""Assemble the static lead-time replay table.

Joins three inputs you supply:

  --registry        JSON list of events:
                    [{"name", "chain": "ethereum"|"tron"|"bitcoin",
                      "event_date": "YYYY-MM-DD",
                      "publication_date": "YYYY-MM-DD",
                      "publication_source": str,
                      "addresses": [{"address": str}, ...]}, ...]
  --first-seen-dir  directory holding first_seen_{eth,tron,btc}.json:
                    {"addresses": {addr: {"first_seen": ts, ...}},
                     "query_id": optional str}
                    (scripts/athena_first_seen.py and
                     scripts/bq_first_seen.py both emit this shape)
  --scores          detector scores per chain space:
                    {"chains": {
                       "eth":  [{"address", "in_graph", "s_fincrime",
                                 "flagged_fincrime": {budget: bool},
                                 "flagged_either":   {budget: bool}}, ...],
                       "tron": [...same...],
                       "btc":  [{"address", "in_graph", "score",
                                 "risk_tier"}, ...]}}

Detection convention (static replay): detection_time = the earliest
first_seen among the event's FLAGGED addresses — today's scores applied
to the address's first on-chain appearance. lead = publication_date −
detection_time; positive = the detector was ahead of public reporting.
This is a retrospective association, not a claim about the model that
existed at event time — state that wherever the output is quoted.

Flagging:
  ethereum/tron  fincrime-head flag at the --alert-budget key of
                 `flagged_fincrime` (plus the either-head variant from
                 `flagged_either`). An ambiguous EVM-hex address uses
                 whichever space holds it (eth first, then tron).
  bitcoin        population-quantile tier >= --btc-tier-min. This is a
                 quantile convention, not an FPR calibration — the output
                 says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

CHAINS = ("eth", "tron", "btc")


def load(registry: Path, first_seen_dir: Path, scores: Path) -> tuple[list, dict, dict]:
    events = json.loads(registry.read_text())
    fs = {c: json.loads((first_seen_dir / f"first_seen_{c}.json").read_text())["addresses"]
          for c in CHAINS}
    chains = json.loads(scores.read_text())["chains"]
    smap = {c: {r["address"]: r for r in chains[c]} for c in chains}
    return events, fs, smap


def addr_verdict(addr: str, smap: dict, fs: dict, budget: str, btc_tier_min: int) -> dict:
    """Resolve one canonical address across the spaces that may hold it."""
    v: dict = {"address": addr, "space": None, "in_graph": False,
               "first_seen": None, "flagged_fin": False, "flagged_either": False,
               "score": None, "btc_tier": None}
    if addr.startswith("0x"):
        for space in ("eth", "tron"):
            r = smap[space].get(addr)
            if r and r.get("in_graph"):
                v.update(space=space, in_graph=True, score=r.get("s_fincrime"),
                         flagged_fin=bool(r["flagged_fincrime"][budget]),
                         flagged_either=bool(r["flagged_either"][budget]))
                break
        for space in ("eth", "tron"):
            rec = fs[space].get(addr)
            if rec:
                v["first_seen"] = rec["first_seen"]
                break
    else:
        r = smap["btc"].get(addr)
        if r and r.get("in_graph"):
            tier = r.get("risk_tier")
            v.update(space="btc", in_graph=True, score=r.get("score"),
                     btc_tier=tier, flagged_fin=bool(tier and tier >= btc_tier_min),
                     flagged_either=bool(tier and tier >= btc_tier_min))
        rec = fs["btc"].get(addr)
        if rec:
            v["first_seen"] = rec["first_seen"]
    return v


def parse_ts(ts: str) -> datetime:
    # Warehouse varchar timestamps arrive as "YYYY-MM-DD HH:MM:SS.mmm" or ISO.
    t = ts.replace("Z", "+00:00").replace(" ", "T", 1)
    dt = datetime.fromisoformat(t)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def build_rows(events: list, fs: dict, smap: dict, budget: str, btc_tier_min: int) -> list[dict]:
    rows = []
    for e in events:
        verdicts = [addr_verdict(a["address"], smap, fs, budget, btc_tier_min)
                    for a in e["addresses"]]
        flagged_fs = sorted(
            v["first_seen"] for v in verdicts if v["flagged_fin"] and v["first_seen"]
        )
        flagged_fs_either = sorted(
            v["first_seen"] for v in verdicts if v["flagged_either"] and v["first_seen"]
        )
        pub = datetime.fromisoformat(e["publication_date"]).replace(tzinfo=UTC)

        def lead(first_seen_list: list[str], pub: datetime = pub) -> float | None:
            if not first_seen_list:
                return None
            det = parse_ts(first_seen_list[0])
            return round((pub - det).total_seconds() / 86400.0, 2)

        rows.append(
            {
                "name": e["name"],
                "chain": e["chain"],
                "event_date": e["event_date"],
                "publication_date": e["publication_date"],
                "publication_source": e["publication_source"],
                "n_addresses": len(verdicts),
                "n_in_graph": sum(v["in_graph"] for v in verdicts),
                "n_with_first_seen": sum(bool(v["first_seen"]) for v in verdicts),
                "n_flagged_fin": sum(v["flagged_fin"] for v in verdicts),
                "n_flagged_either": sum(v["flagged_either"] for v in verdicts),
                "detection_time_fin": flagged_fs[0] if flagged_fs else None,
                "lead_days_fin": lead(flagged_fs),
                "lead_days_either": lead(flagged_fs_either),
                "addresses": verdicts,
            }
        )
    return rows


def aggregate(rows: list[dict], chain: str | None, key: str,
              scraped_only: bool = False,
              scraped_sources: tuple[str, ...] = ("scraped", "scrape")) -> dict:
    sel = [
        r for r in rows
        if (chain is None or r["chain"] == chain)
        and r[key] is not None
        and (not scraped_only or r["publication_source"] in scraped_sources)
    ]
    tot = [
        r for r in rows
        if (chain is None or r["chain"] == chain)
        and (not scraped_only or r["publication_source"] in scraped_sources)
    ]
    leads = [r[key] for r in sel]
    return {
        "n_events": len(tot),
        "n_detected": len(sel),
        "median_lead_days": round(median(leads), 1) if leads else None,
        "n_positive_lead": sum(1 for x in leads if x > 0),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", required=True, type=Path)
    ap.add_argument("--first-seen-dir", required=True, type=Path)
    ap.add_argument("--scores", required=True, type=Path)
    ap.add_argument("--out-json", required=True, type=Path)
    ap.add_argument("--out-md", required=True, type=Path)
    ap.add_argument("--alert-budget", default="0.001",
                    help="key into the flagged_* dicts of the scores file (default 0.001)")
    ap.add_argument("--btc-tier-min", type=int, default=4,
                    help="minimum population-quantile tier that counts as flagged on bitcoin")
    ap.add_argument("--flagging-note-evm",
                    default="fincrime-head score >= population threshold at the alert budget; "
                            "the either-head variant adds the second head",
                    help="free-text description of the EVM flagging convention, recorded in the output")
    ap.add_argument("--flagging-note-btc",
                    default="population-quantile tier — a quantile convention, not an FPR calibration",
                    help="free-text description of the bitcoin flagging convention")
    ap.add_argument("--scores-note", default="",
                    help="free-text provenance of the scores file, recorded in the output")
    ap.add_argument("--utc-stamp", default=None,
                    help="fixed UTC stamp for reproducible output (default: now)")
    args = ap.parse_args(argv)

    events, fs, smap = load(args.registry, args.first_seen_dir, args.scores)
    rows = build_rows(events, fs, smap, args.alert_budget, args.btc_tier_min)

    def agg(chain: str | None, key: str, scraped_only: bool = False) -> dict:
        return aggregate(rows, chain, key, scraped_only)

    summary = {
        "fpr": args.alert_budget,
        "flagging": {"evm": args.flagging_note_evm, "btc": args.flagging_note_btc},
        "per_chain_fin": {c: agg(c, "lead_days_fin") for c in ("ethereum", "tron", "bitcoin")},
        "per_chain_either": {c: agg(c, "lead_days_either") for c in ("ethereum", "tron", "bitcoin")},
        "all_fin": agg(None, "lead_days_fin"),
        "all_either": agg(None, "lead_days_either"),
        "scraped_only_fin": agg(None, "lead_days_fin", scraped_only=True),
    }
    out = {
        "utc": args.utc_stamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "producer": "openeval.leadtime",
        "inputs": {
            "first_seen_query_ids": {
                c: json.loads((args.first_seen_dir / f"first_seen_{c}.json").read_text()).get("query_id")
                for c in CHAINS
            },
            "scores": args.scores_note or str(args.scores),
        },
        "summary": summary,
        "events": rows,
    }
    args.out_json.write_text(json.dumps(out, indent=1))
    sha = hashlib.sha256(args.out_json.read_bytes()).hexdigest()

    md = [
        "# Lead-time replay",
        "",
        f"**JSON:** `{args.out_json}` sha256 `{sha}`",
        "",
        f"**Flagging:** EVM = {args.flagging_note_evm}. BTC = {args.flagging_note_btc}.",
        "",
        "## Summary",
        "",
        f"| Group | Events | Detected (fin@{args.alert_budget}) | Median lead, days | Positive lead |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, a in (
        ("ethereum", summary["per_chain_fin"]["ethereum"]),
        ("tron", summary["per_chain_fin"]["tron"]),
        (f"bitcoin (tier>={args.btc_tier_min})", summary["per_chain_fin"]["bitcoin"]),
        ("ALL", summary["all_fin"]),
        ("ALL scraped-pub only", summary["scraped_only_fin"]),
    ):
        md.append(
            f"| {label} | {a['n_events']} | {a['n_detected']} | "
            f"{a['median_lead_days'] if a['median_lead_days'] is not None else '—'} | {a['n_positive_lead']} |"
        )
    md += [
        "",
        f"Either-head variant (ALL): detected {summary['all_either']['n_detected']}, "
        f"median {summary['all_either']['median_lead_days']} d.",
        "",
        f"## Per-event table (detected events, fin@{args.alert_budget})",
        "",
        "| # | Event | Chain | Addrs | In graph | Flagged | Detection (first_seen) | Pub | Lead, d | Pub source |",
        "|---:|---|---|---:|---:|---:|---|---|---:|---|",
    ]
    i = 0
    for r in rows:
        if r["lead_days_fin"] is None:
            continue
        i += 1
        md.append(
            f"| {i} | {r['name'][:42]} | {r['chain']} | {r['n_addresses']} | {r['n_in_graph']} | "
            f"{r['n_flagged_fin']} | {r['detection_time_fin'][:10]} | {r['publication_date']} | "
            f"{r['lead_days_fin']:.0f} | {r['publication_source']} |"
        )
    md += [
        "",
        "## Conventions, stated so they cannot be misread",
        "",
        "- Static replay: today's scores applied to each address's first on-chain appearance. "
        "Not a claim about the model that existed at event time.",
        "- Publication dates come from the registry as supplied; proxy dates (if any) are marked "
        "by their `publication_source` and separated in the scraped-only row.",
    ]
    args.out_md.write_text("\n".join(md))
    print(json.dumps(summary, indent=1))
    print(f"JSON sha256 {sha}")
    print(f"-> {args.out_json}\n-> {args.out_md}")


if __name__ == "__main__":
    main()
