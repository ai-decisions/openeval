"""openeval — evaluation harness for a graph-based financial-crime detector.

The pipeline, in the order the artefacts flow:

    leadtime        assemble the static lead-time replay from an event
                    registry, per-chain first-seen indices and detector
                    scores (python -m openeval.leadtime)
    date_audit      patch proxy publication dates with audited real dates
                    and recompute leads (python -m openeval.date_audit)
    event_lift      event-level detection lift against the honest
                    per-event null 1-(1-p)^m (python -m openeval.event_lift)
    event_lift_gate lift + group stats for a corrected replay, gated on
                    invariants that dates cannot move
                    (python -m openeval.event_lift_gate)
    event_table     per-event audit table as a LaTeX fragment
                    (python -m openeval.event_table)

Every module is stdlib-only, reads the JSON files you name and writes the
files you name — there are no default input locations and no network
calls. The input schemas are documented in each module's docstring.
"""

__version__ = "0.1.0"
