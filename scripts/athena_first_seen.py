#!/usr/bin/env python3
"""Run the shipped first-seen SQL templates (eth/tron/btc) on YOUR Athena.

Renders queries/first_seen_<chain>.sql.tmpl with the address list and
database you supply, runs it in the workgroup and region you name, pages
the full result set and writes first_seen_<chain>.json in the shape
openeval.leadtime consumes:

    {"chain", "query_id", "workgroup", "predicted_scan_bytes",
     "scanned_bytes", "cost_usd", "elapsed_ms", "n_addresses_found",
     "addresses": {addr: {"first_seen": ts, "n_activity": int}}}

Configuration is fail-closed: database, workgroup, region and the address
file are required, with no defaults. Addresses are validated against a
strict per-chain character class before being rendered into SQL — an
"address" that does not match is a hard error, not a silent skip, so the
shipped templates cannot be used as an injection vector.

Cost discipline: cap scan bytes in the WORKGROUP (bytes-scanned cutoff),
and pass --predicted-bytes if you have an independent estimate — the
runner prints prediction next to the engine-measured DataScannedInBytes
so the pair is on the record. --usd-per-tb defaults to 5.0 (a common
on-demand list price; check your region's).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "queries"

ADDRESS_RE = {
    # EVM-hex canonical form, lowercase.
    "eth": re.compile(r"^0x[0-9a-f]{40}$"),
    "tron": re.compile(r"^0x[0-9a-f]{40}$"),
    # Base58 / bech32 character classes only — tight enough that a quote,
    # space or parenthesis can never reach the rendered SQL.
    "btc": re.compile(r"^(?:[13][1-9A-HJ-NP-Za-km-z]{25,34}|bc1[02-9ac-hj-np-z]{8,87})$"),
}


def load_addresses(path: Path, chain: str) -> list[str]:
    """One address per line (blank lines and #-comments ignored), or a JSON list."""
    text = path.read_text()
    if text.lstrip().startswith("["):
        addrs = json.loads(text)
    else:
        addrs = [line.strip() for line in text.splitlines()
                 if line.strip() and not line.strip().startswith("#")]
    rx = ADDRESS_RE[chain]
    # fullmatch, not match: `$` alone still accepts a trailing newline inside a
    # JSON string value, which would survive into the rendered SQL literal.
    bad = [a for a in addrs if not isinstance(a, str) or not rx.fullmatch(a)]
    if bad:
        sys.exit(f"FATAL: {len(bad)} address(es) fail the {chain} format check, "
                 f"first: {bad[0]!r} — refusing to render SQL")
    if not addrs:
        sys.exit("FATAL: the address file is empty")
    return sorted(set(addrs))


def render(chain: str, addresses: list[str], database: str, templates_dir: Path) -> str:
    tmpl = (templates_dir / f"first_seen_{chain}.sql.tmpl").read_text()
    # str.replace substitutes EVERY occurrence, so the template must carry the
    # address placeholder exactly once — a second one (say, quoted in a header
    # comment) would inject bare VALUES rows mid-comment and produce SQL that
    # parses right up until Athena rejects it.
    if tmpl.count("{{ADDRESS_VALUES}}") != 1:
        sys.exit("FATAL: template must contain the ADDRESS_VALUES placeholder exactly once")
    if "{{DATABASE}}" not in tmpl:
        sys.exit("FATAL: template carries no DATABASE placeholder")
    values = ",\n".join(f"  ('{a}')" for a in addresses)
    sql = tmpl.replace("{{ADDRESS_VALUES}}", values).replace("{{DATABASE}}", database)
    if "{{" in sql:
        sys.exit("FATAL: unrendered placeholder left in SQL — template and renderer disagree")
    return sql


def run_query(ath, sql: str, workgroup: str, timeout_s: int) -> dict:
    qid = ath.start_query_execution(QueryString=sql, WorkGroup=workgroup)[
        "QueryExecutionId"
    ]
    t0 = time.time()
    while True:
        qe = ath.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = qe["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() - t0 > timeout_s:
            raise SystemExit(f"TIMEOUT after {timeout_s}s (qid={qid}, state={state})")
        time.sleep(3)
    stats = qe.get("Statistics", {})
    if state != "SUCCEEDED":
        raise SystemExit(
            f"qid={qid} state={state} reason={qe['Status'].get('StateChangeReason')}"
        )
    # Page the FULL result set; the first row is the header.
    rows: list[list[str | None]] = []
    token = None
    while True:
        kw = {"QueryExecutionId": qid, "MaxResults": 1000}
        if token:
            kw["NextToken"] = token
        res = ath.get_query_results(**kw)
        for r in res["ResultSet"]["Rows"]:
            rows.append([c.get("VarCharValue") for c in r["Data"]])
        token = res.get("NextToken")
        if not token:
            break
    header, data = rows[0], rows[1:]
    return {
        "query_id": qid,
        "scanned_bytes": int(stats.get("DataScannedInBytes", 0)),
        "elapsed_ms": int(stats.get("TotalExecutionTimeInMillis", 0)),
        "header": header,
        "rows": data,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True, choices=sorted(ADDRESS_RE))
    ap.add_argument("--addresses", required=True, type=Path,
                    help="address list: one per line, or a JSON array")
    ap.add_argument("--database", required=True,
                    help="Athena database holding your chain tables (see the template for "
                         "the table names it expects)")
    ap.add_argument("--workgroup", required=True,
                    help="Athena workgroup; set its bytes-scanned cutoff as your cost cap")
    ap.add_argument("--region", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--queries-dir", type=Path, default=TEMPLATES_DIR)
    ap.add_argument("--usd-per-tb", type=float, default=5.0)
    ap.add_argument("--predicted-bytes", type=int, default=None,
                    help="independent scan-bytes estimate, printed next to the measured value")
    ap.add_argument("--timeout-s", type=int, default=1800)
    args = ap.parse_args(argv)

    try:
        import boto3
    except ImportError:
        sys.exit("boto3 is not installed — `pip install openeval[athena]` for this runner")

    addresses = load_addresses(args.addresses, args.chain)
    sql = render(args.chain, addresses, args.database, args.queries_dir)
    if args.predicted_bytes is not None:
        print(
            f"[{args.chain}] prediction (BEFORE scan): {args.predicted_bytes/1e9:.1f} GB "
            f"~ ${args.predicted_bytes/1e12*args.usd_per_tb:.2f}",
            flush=True,
        )
    ath = boto3.client("athena", region_name=args.region)
    r = run_query(ath, sql, args.workgroup, args.timeout_s)
    cost = r["scanned_bytes"] / 1e12 * args.usd_per_tb
    addr_rows = {
        row[0]: {"first_seen": row[1], "n_activity": int(row[2])}
        for row in r["rows"]
    }
    out = {
        "chain": args.chain,
        "query_id": r["query_id"],
        "workgroup": args.workgroup,
        "predicted_scan_bytes": args.predicted_bytes,
        "scanned_bytes": r["scanned_bytes"],
        "cost_usd": round(cost, 4),
        "elapsed_ms": r["elapsed_ms"],
        "n_addresses_found": len(addr_rows),
        "addresses": addr_rows,
    }
    args.out.write_text(json.dumps(out, indent=1))
    print(
        f"[{args.chain}] qid={r['query_id']} scanned={r['scanned_bytes']/1e9:.1f} GB "
        f"(${cost:.2f}) rows={len(addr_rows)} -> {args.out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
