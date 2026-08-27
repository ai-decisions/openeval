#!/usr/bin/env python3
"""First-seen timestamps from BigQuery PUBLIC datasets (no private warehouse).

For each address you supply, finds MIN(block_timestamp) across the public
on-chain tables — the first time the address appeared on-chain, i.e. the
earliest point any detector could have seen it. This is the one first-seen
path a third party can run without access to anyone's substrate:

  ethereum  bigquery-public-data.crypto_ethereum
            transactions (from/to) UNION traces (from/to) — traces catch
            addresses whose first activity was an internal transfer
  tron      bigquery-public-data.goog_blockchain_tron_mainnet_us
            transactions (from/to); addresses are 0x-hex lowercase
            (20-byte payload, mainnet prefix stripped)

Output (the shape openeval.leadtime consumes, plus billing metadata):

    {"query_bytes_billed", "query_cost_est_usd", "n_addresses_queried",
     "n_addresses_found_on_chain",
     "addresses": {addr: {"first_seen", "last_seen", "n_activity"}},
     "addresses_not_on_chain": [...]}

Runs under YOUR Google Cloud project and billing (--project is required,
no default). Addresses are validated as lowercase EVM-hex before being
rendered into the query, so the address file cannot smuggle SQL.

Cost: the tables are large; an address-filtered scan is typically a few
hundred GB (a few USD). --start-date bounds the scan window and cost, but
CAUTION: a first_seen computed with a start date is only valid if the
address cannot predate it — an address active earlier will get a WRONG
first_seen, silently. Omit it (full range) unless you know the bound.
A bound at or before the chain's public-dataset genesis is always safe
(the Tron public dataset starts 2018-06-25); anything later trades cost
against exactly this truncation risk.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HEX_RE = re.compile(r"^0x[0-9a-f]{40}$")

# On-demand list price per TiB scanned; check your project's actual rate.
USD_PER_TIB = 6.25

TABLES = {
    "ethereum": {
        "sources": [
            ("`bigquery-public-data.crypto_ethereum.transactions`", "from_address"),
            ("`bigquery-public-data.crypto_ethereum.transactions`", "to_address"),
            ("`bigquery-public-data.crypto_ethereum.traces`", "from_address"),
            ("`bigquery-public-data.crypto_ethereum.traces`", "to_address"),
        ],
    },
    "tron": {
        "sources": [
            ("`bigquery-public-data.goog_blockchain_tron_mainnet_us.transactions`", "from_address"),
            ("`bigquery-public-data.goog_blockchain_tron_mainnet_us.transactions`", "to_address"),
        ],
    },
}


def load_addresses(path: Path) -> list[str]:
    """One address per line (blank lines and #-comments ignored), or a JSON list."""
    text = path.read_text()
    if text.lstrip().startswith("["):
        addrs = [a.lower() if isinstance(a, str) else a for a in json.loads(text)]
    else:
        addrs = [line.strip().lower() for line in text.splitlines()
                 if line.strip() and not line.strip().startswith("#")]
    # fullmatch, not match: `$` alone still accepts a trailing newline inside a
    # JSON string value, which would survive into the rendered SQL literal.
    bad = [a for a in addrs if not isinstance(a, str) or not HEX_RE.fullmatch(a)]
    if bad:
        sys.exit(f"FATAL: {len(bad)} address(es) fail the 0x-hex format check, "
                 f"first: {bad[0]!r} — refusing to render SQL")
    if not addrs:
        sys.exit("FATAL: the address file is empty")
    return sorted(set(addrs))


def build_query(chain: str, addresses: list[str], start_date: str | None) -> str:
    addr_array = "(" + ",".join(f"'{a}'" for a in addresses) + ")"
    date_filter = f"\n        AND block_timestamp >= '{start_date}'" if start_date else ""
    selects = [
        f"""      SELECT LOWER({col}) AS addr, block_timestamp
      FROM {table}
      WHERE LOWER({col}) IN {addr_array}{date_filter}"""
        for table, col in TABLES[chain]["sources"]
    ]
    union = "\n      UNION ALL\n".join(selects)
    return f"""
    WITH all_activity AS (
{union}
    )
    SELECT
      addr,
      MIN(block_timestamp) AS first_seen,
      MAX(block_timestamp) AS last_seen,
      COUNT(*) AS n_activity
    FROM all_activity
    WHERE addr IS NOT NULL
    GROUP BY addr
    ORDER BY first_seen
    """


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chain", required=True, choices=sorted(TABLES))
    ap.add_argument("--addresses", required=True, type=Path,
                    help="address list: one 0x-hex per line, or a JSON array")
    ap.add_argument("--project", required=True,
                    help="YOUR Google Cloud project id (bears the query cost)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--start-date", default=None,
                    help="optional lower bound YYYY-MM-DD; see the cost/correctness caution above")
    args = ap.parse_args(argv)

    try:
        from google.cloud import bigquery
    except ImportError:
        sys.exit("google-cloud-bigquery is not installed — "
                 "`pip install openeval[bq]` for this runner")

    addrs = load_addresses(args.addresses)
    print(f"Querying {args.chain} first-seen for {len(addrs)} unique addresses...")
    if args.start_date:
        print(f"  CAUTION: --start-date {args.start_date} — first_seen is wrong for any "
              f"address active before it")

    client = bigquery.Client(project=args.project)
    query = build_query(args.chain, addrs, args.start_date)
    t0 = time.time()
    job = client.query(query)
    rows = list(job.result())
    elapsed = time.time() - t0
    bytes_billed = job.total_bytes_billed or 0
    cost_est = bytes_billed / (1024**4) * USD_PER_TIB
    print(f"  Done in {elapsed:.1f}s; billed {bytes_billed/1e9:.1f} GB ~ ${cost_est:.2f}")

    result: dict = {
        "query_bytes_billed": int(bytes_billed),
        "query_cost_est_usd": cost_est,
        "n_addresses_queried": len(addrs),
        "n_addresses_found_on_chain": len(rows),
        "addresses": {},
    }
    for r in rows:
        result["addresses"][r["addr"]] = {
            "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            "n_activity": int(r["n_activity"]),
        }
    missing = [a for a in addrs if a not in result["addresses"]]
    result["addresses_not_on_chain"] = missing

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, default=str))
    print(f"\n  {len(rows)}/{len(addrs)} addresses found on-chain")
    print(f"  {len(missing)} addresses have NO on-chain activity")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
