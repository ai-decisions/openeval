"""Full-pipeline determinism: one digest over every path-independent output.

The synthetic world runs end to end (leadtime -> date_audit -> event_lift
-> event_lift_gate -> event_table) with the one timestamp pinned; the
sha256 below must be identical on clean clones under Python 3.11 and
3.12. Path-bearing metadata (input file locations in tmp_path) is
excluded from the digest — everything numeric and textual that the
pipeline PRODUCES is in.

The pin is filled ONLY from a CI log: until then this test FAILS and
prints the digest, so a wrong local value can never be committed as if it
had been verified. (A pin that "prints and passes" is a step that cannot
fail — that is not a gate.)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from openeval import event_lift, event_lift_gate, event_table
from tests.conftest import build_world, run_date_audit, run_leadtime
from tests.test_event_lift_gate import _true_invariants

# Filled from the "Repro (synthetic pipeline digest)" CI step, never locally;
# the 3.11 and 3.12 runners printed this identical value.
PINNED_DIGEST: str | None = "085f47ac7b583c66d4795c2d8d0948f05548f116dcdce78e8bfdf9a3faafc99b"


def _pipeline_digest(world: dict) -> str:
    d: Path = world["dir"]
    replay = run_leadtime(world)
    audited = run_date_audit(world, replay)

    lift_json = d / "lift.json"
    event_lift.main(["--replay", str(replay),
                     "--out-json", str(lift_json), "--out-md", str(d / "lift.md")])

    inv = d / "invariants.json"
    inv.write_text(json.dumps(_true_invariants(audited)))
    gate_json = d / "gate.json"
    event_lift_gate.main(["--replay", str(audited), "--invariants", str(inv),
                          "--out", str(gate_json)])

    tex = d / "events.tex"
    event_table.main(["--replay", str(audited), "--registry", str(world["registry"]),
                      "--out", str(tex), "--expect-evm-events", "5"])

    replay_data = json.loads(replay.read_text())
    audited_data = json.loads(audited.read_text())
    audited_data["summary"]["date_audit"].pop("patch_file")  # tmp_path-dependent
    lift_data = json.loads(lift_json.read_text())
    gate_data = json.loads(gate_json.read_text())
    # lift.md is deliberately NOT hashed: it prints the source artefact's
    # location, which is tmp_path-dependent. Its numbers are lift.json's.
    material = json.dumps(
        [
            {"summary": replay_data["summary"], "events": replay_data["events"]},
            {"summary": audited_data["summary"], "events": audited_data["events"]},
            {"groups": lift_data["groups"], "null_model": lift_data["null_model"]},
            {"groups": gate_data["groups"]},
            tex.read_text(),
        ],
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def test_pipeline_digest_is_pinned_and_reproduced(world: dict) -> None:
    digest = _pipeline_digest(world)
    print(f"\nPIPELINE_DIGEST {digest}")
    assert PINNED_DIGEST is not None, (
        "PINNED_DIGEST is unset. Take the PIPELINE_DIGEST value printed by the "
        "CI 'Repro' step (both Python versions must print the same value) and "
        "commit it into tests/test_pipeline_determinism.py."
    )
    assert digest == PINNED_DIGEST


def test_two_runs_in_separate_directories_agree(world: dict, tmp_path: Path) -> None:
    first = _pipeline_digest(world)
    second = _pipeline_digest(build_world(tmp_path / "second"))
    assert second == first
