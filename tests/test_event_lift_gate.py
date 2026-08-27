"""openeval.event_lift_gate — invariants hold or nothing is emitted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openeval import event_lift_gate
from openeval.event_lift import group_stats


def _true_invariants(audited: Path) -> dict:
    data = json.loads(audited.read_text())
    p = float(data["summary"]["fpr"])
    evm = [e for e in data["events"] if e["chain"] in ("ethereum", "tron")]
    out = {}
    for name, evs in (("ethereum", [e for e in evm if e["chain"] == "ethereum"]),
                      ("tron", [e for e in evm if e["chain"] == "tron"]),
                      ("evm_total", evm)):
        g = group_stats(evs, p)
        out[name] = {k: g[k] for k in
                     ("n_events", "n_detected", "null_event_rate_in_graph_m")}
    return out


def _run(audited: Path, invariants: dict, tmp_path: Path) -> dict:
    inv = tmp_path / "invariants.json"
    inv.write_text(json.dumps(invariants))
    out = tmp_path / "gate.json"
    event_lift_gate.main(["--replay", str(audited),
                          "--invariants", str(inv),
                          "--out", str(out)])
    return json.loads(out.read_text())


def test_matching_invariants_pass_and_groups_emitted(audited: Path, tmp_path: Path) -> None:
    out = _run(audited, _true_invariants(audited), tmp_path)
    g = out["groups"]["evm_total"]
    assert (g["n_events"], g["n_detected"]) == (5, 3)
    # audited scraped set: Alpha + Delta + patched Beta
    assert out["groups"]["scraped_only_incl_audited"]["n_events"] == 3
    # lead medians come from the CORRECTED dates
    assert g["median_lead_days"] == 77.0  # [161, -10, 77] -> 77
    assert len(out["invariants"]["sha256"]) == 64


def test_unknown_group_in_invariants_is_a_named_error(audited: Path, tmp_path: Path) -> None:
    inv = _true_invariants(audited)
    inv["no_such_group"] = inv["evm_total"]
    with pytest.raises(SystemExit, match="unknown group 'no_such_group'"):
        _run(audited, inv, tmp_path)


def test_wrong_detection_count_is_fatal(audited: Path, tmp_path: Path) -> None:
    inv = _true_invariants(audited)
    inv["tron"]["n_detected"] += 1
    with pytest.raises(SystemExit, match="does not reproduce"):
        _run(audited, inv, tmp_path)


def test_null_rate_drift_beyond_tolerance_is_fatal(audited: Path, tmp_path: Path) -> None:
    inv = _true_invariants(audited)
    inv["evm_total"]["null_event_rate_in_graph_m"] += 1e-4
    with pytest.raises(SystemExit, match="does not reproduce"):
        _run(audited, inv, tmp_path)


def test_shipped_example_matches_the_gate_schema(tmp_path: Path, audited: Path) -> None:
    """examples/invariants_reported.json must stay loadable by the gate —
    its VALUES are the paper's, so they cannot match the synthetic world,
    but every pinned group and key must be one the gate understands."""
    shipped = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "invariants_reported.json")
        .read_text())
    assert set(shipped) <= {"ethereum", "tron", "evm_total"}
    for g in shipped.values():
        assert set(g) == {"n_events", "n_detected", "null_event_rate_in_graph_m"}
    with pytest.raises(SystemExit, match="does not reproduce"):
        _run(audited, shipped, tmp_path)
