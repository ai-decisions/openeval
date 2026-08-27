# openeval

Evaluation harness for a graph-based financial-crime detector, built and
used in production by [AI DECISIONS](https://aidecisions.ai/): a static
lead-time replay against an external registry of public events
(designations, hacks, exchange collapses), a publication-date audit
patcher, an event-level detection lift with an honest per-event null
model, an invariance gate for corrected replays, a LaTeX audit-table
emitter, and two first-seen warehouse runners. This repository is the
**measurement method**, not the events, the scores or the result.

## Boundary — what is open here, and what is not

**Open (this repo):**

- **Lead-time replay assembly** (`openeval.leadtime`): joins an event
  registry, per-chain first-seen indices and detector scores into the
  replay table — detection time = earliest first on-chain appearance
  among an event's flagged addresses; lead = publication date minus
  detection time. The replay is static (today's scores applied to first
  appearances) and the output says so.
- **Publication-date audit** (`openeval.date_audit`): patches
  proxy-dated events with audited real dates and recomputes leads —
  detection counts are date-independent and are carried unchanged.
- **Event-level lift** (`openeval.event_lift`): the honest null for
  "event detected iff any of its m addresses is flagged" is
  1-(1-p)^m per event, not the address-level budget p. The module
  refuses to run on a replay that does not reproduce its own summary.
- **Invariance gate** (`openeval.event_lift_gate`): recomputes lift
  groups for a date-corrected replay and fails hard unless the
  date-invariant figures match the invariants you pin
  (`examples/invariants_reported.json` carries the values reported in
  the accompanying paper).
- **Audit-table emitter** (`openeval.event_table`): the per-event LaTeX
  table (publication date, date source, in-graph/listed addresses,
  flagged count, lead) that lets a reviewer audit the replay instead of
  trusting it.
- **First-seen runners**: `scripts/athena_first_seen.py` renders the
  shipped SQL templates (`queries/*.sql.tmpl`) against a warehouse YOU
  configure; `scripts/bq_first_seen.py` runs against **public** BigQuery
  datasets (`bigquery-public-data`) — the one path a third party can run
  with no private warehouse at all.

**Not open (deliberately):**

- The event registry itself (event names with their on-chain addresses),
  the detector scores, the first-seen indices built from our substrate,
  the graph substrate, trained weights, and production threshold values.
  The paper's appendix lists every replay event so the aggregates can be
  audited; the address-level registry is not distributed.

## No data

The repository ships **no datasets at all**: no event registry, no
address lists, no scores, no first-seen indices, no weights, no
thresholds. Every producer takes its inputs as named parameters and
fails closed when one is missing; the warehouse runners have no default
database, workgroup, project or region. Input schemas are documented in
each module's docstring, and the test suite builds synthetic fixtures
that satisfy them — read `tests/conftest.py` as executable schema
documentation.

## Install

```bash
pip install -e .          # core: stdlib-only, every replay/lift/audit module
pip install -e .[athena]  # + boto3: run the shipped SQL templates on your Athena
pip install -e .[bq]      # + google-cloud-bigquery: public-dataset first-seen
pip install -e .[dev]     # everything the test suite touches
```

Python 3.11+.

## Quickstart

```bash
pip install -e .[dev]
pytest -q
```

The suite runs entirely on synthetic inputs built in memory — no data,
no credentials, no network. The pipeline, end to end:

```text
scripts/bq_first_seen.py /
scripts/athena_first_seen.py -> first_seen_<chain>.json   (per chain)
openeval.leadtime           -> replay.json + report.md
openeval.date_audit         -> replay_audited.json + report.md
openeval.event_lift         -> lift.json + lift.md
openeval.event_lift_gate    -> lift_audited.json   (gated on invariants)
openeval.event_table        -> events_appendix.tex
```

Every module prints its exact required arguments with `--help`
(`python -m openeval.leadtime --help`).

## Reproducibility

- Every producer is deterministic given its inputs (`openeval.leadtime`
  takes `--utc-stamp` to pin its one timestamp field); CI runs the full
  synthetic pipeline on clean clones under Python 3.11 and 3.12 and
  compares the output digest against a pinned value.
- `openeval.event_lift` re-derives its input's summary from the event
  rows before computing anything and exits non-zero on mismatch, so a
  silently edited artefact cannot produce a plausible lift.
- The values in `examples/invariants_reported.json` are the event counts
  and detections reported in the accompanying paper, with the exact
  per-group null rates this code emits for them; running the gate
  against a corrected replay of the same events must reproduce the
  counts exactly and the null rates within the gate's stated tolerance.

## Contributing

Contributions are accepted under the Developer Certificate of Origin
(sign-off line in commits, `git commit -s`). License: Apache-2.0, see
`LICENSE` and `NOTICE`.
