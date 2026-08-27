# Security Policy

## Reporting a vulnerability

Please report vulnerabilities **privately** to **mail@aidecisions.ai**.
Do not open a public issue for security problems. We will acknowledge
your report and coordinate disclosure with you; a fix or a mitigation
plan is published before details are.

This policy covers the code in this repository. Issues in the hosted
AI DECISIONS platform (aidecisions.ai) go to the same address.

## What this code does — and does not do

- The replay, lift, date-audit and table-emitter modules read local JSON
  files **you supply** as parameters and write local files you name.
  They make no network calls at all.
- The two warehouse runners are opt-in extras. `scripts/bq_first_seen.py`
  calls Google BigQuery under **your** project and credentials;
  `scripts/athena_first_seen.py` calls AWS Athena in a database,
  workgroup and region **you** must name — there is no default warehouse
  anywhere in this tree. Both use the standard credential chain of their
  SDKs; this repository never reads a credential itself.
- Address inputs to the warehouse runners are validated against strict
  per-chain character classes before being rendered into SQL, so a
  malicious "address" cannot smuggle SQL into the shipped templates.
- The repository ships **no data**: no event registry, no addresses, no
  scores, no first-seen indices, no weights, no thresholds (see
  `README.md` → Boundary).
- No telemetry, no callbacks.
