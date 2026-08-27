"""openeval.event_lift — the honest per-event null and the self-check gate.

Hand math for the synthetic world (p = 0.001), EVM events only:
  m_in_graph: Alpha 2, Beta 1, Gamma 1, Delta 1, Epsilon 2; detected 3/5.
  null_ig = 2*(1-0.999^2) + 3*0.001 = 0.006998 -> /5 = 0.0013996 -> 0.0014
  lift    = 0.6 / 0.0013996 = 428.69... -> 428.7
  naive   = 0.6 / 0.001 = 600
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openeval import event_lift


def _run(replay: Path, out_dir: Path) -> dict:
    out_json = out_dir / "lift.json"
    event_lift.main(["--replay", str(replay),
                     "--out-json", str(out_json),
                     "--out-md", str(out_dir / "lift.md")])
    return json.loads(out_json.read_text())


class TestGroupStats:
    def test_single_address_event_null_equals_p(self) -> None:
        g = event_lift.group_stats(
            [{"n_flagged_fin": 1, "n_in_graph": 1, "n_addresses": 1}], 0.001)
        assert g["null_event_rate_in_graph_m"] == 0.001
        assert g["lift_naive_address_level"] == g["lift_event_level_in_graph_m"] == 1000.0

    def test_multi_address_event_null_compounds(self) -> None:
        # 1-(1-0.001)^3 = 0.002997001 -> round6
        g = event_lift.group_stats(
            [{"n_flagged_fin": 0, "n_in_graph": 3, "n_addresses": 5}], 0.001)
        assert g["null_event_rate_in_graph_m"] == 0.002997
        assert g["n_detected"] == 0

    def test_all_address_variant_is_more_conservative(self) -> None:
        g = event_lift.group_stats(
            [{"n_flagged_fin": 1, "n_in_graph": 2, "n_addresses": 10}], 0.001)
        assert g["null_event_rate_all_addr_m"] > g["null_event_rate_in_graph_m"]
        assert g["lift_event_level_all_addr_m"] < g["lift_event_level_in_graph_m"]


class TestOnSyntheticReplay:
    def test_evm_total_matches_hand_math(self, replay: Path, tmp_path: Path) -> None:
        out = _run(replay, tmp_path)
        g = out["groups"]["evm_total"]
        assert (g["n_events"], g["n_detected"]) == (5, 3)
        assert g["null_event_rate_in_graph_m"] == 0.0014
        assert g["lift_event_level_in_graph_m"] == 428.7
        assert g["lift_naive_address_level"] == 600.0

    def test_source_artifact_sha_recorded(self, replay: Path, tmp_path: Path) -> None:
        out = _run(replay, tmp_path)
        assert len(out["source_artifact"]["sha256"]) == 64
        assert out["fpr_budget_address_level"] == 0.001

    def test_byte_reproducible(self, replay: Path, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir(), b.mkdir()
        _run(replay, a)
        _run(replay, b)
        assert (a / "lift.json").read_bytes() == (b / "lift.json").read_bytes()
        assert (a / "lift.md").read_bytes() == (b / "lift.md").read_bytes()


class TestFailClosed:
    def test_tampered_summary_is_fatal(self, replay: Path, tmp_path: Path) -> None:
        data = json.loads(replay.read_text())
        data["summary"]["per_chain_fin"]["ethereum"]["n_detected"] += 1
        bad = tmp_path / "tampered.json"
        bad.write_text(json.dumps(data))
        with pytest.raises(SystemExit, match="do not reproduce"):
            _run(bad, tmp_path)

    def test_missing_artifact_is_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="artifact missing"):
            _run(tmp_path / "nope.json", tmp_path)

    def test_non_evm_scraped_event_breaks_the_convention_check(
            self, world: dict, tmp_path: Path) -> None:
        """A bitcoin event with a scraped date violates the artefact
        convention the cross-check enforces — the module must refuse."""
        from tests.conftest import run_leadtime

        events = json.loads(world["registry"].read_text())
        next(e for e in events if e["chain"] == "bitcoin")["publication_source"] = "scraped"
        world["registry"].write_text(json.dumps(events))
        replay = run_leadtime(world)
        with pytest.raises(SystemExit, match="do not reproduce"):
            _run(replay, tmp_path)
