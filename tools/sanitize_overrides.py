"""Repo-specific sanitize-gate overrides for openeval (plain data, no logic).

This repository legitimately ships a BigQuery public-dataset runner
(scripts/bq_first_seen.py and its docs): BigQuery here is a public data
source the published method runs against, not a private-stack marker.
The pattern is disabled for this tree only.
"""

DISABLED_PATTERNS = {"private-stack-marker"}
