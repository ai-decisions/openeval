"""openeval.leadtime — replay assembly on the synthetic world.

Expected values are hand-computed in tests/conftest.py's docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openeval import leadtime


def _load(replay: Path) -> dict:
    return json.loads(replay.read_text())


def _row(data: dict, name: str) -> dict:
    return next(r for r in data["events"] if r["name"] == name)


class TestSummary:
    def test_per_chain_fin_counts(self, replay: Path) -> None:
        s = _load(replay)["summary"]
        assert s["per_chain_fin"]["ethereum"] == {
            "n_events": 3, "n_detected": 1, "median_lead_days": 161.0, "n_positive_lead": 1}
        assert s["per_chain_fin"]["tron"] == {
            "n_events": 2, "n_detected": 2, "median_lead_days": 33.5, "n_positive_lead": 1}
        assert s["per_chain_fin"]["bitcoin"] == {
            "n_events": 1, "n_detected": 1, "median_lead_days": 31.0, "n_positive_lead": 1}

    def test_all_fin(self, replay: Path) -> None:
        # leads [161, -10, 77, 31] -> median 54.0, positive 3
        assert _load(replay)["summary"]["all_fin"] == {
            "n_events": 6, "n_detected": 4, "median_lead_days": 54.0, "n_positive_lead": 3}

    def test_scraped_only_excludes_proxy_dates(self, replay: Path) -> None:
        # scraped events: Alpha (161), Delta (-10) -> median 75.5
        assert _load(replay)["summary"]["scraped_only_fin"] == {
            "n_events": 2, "n_detected": 2, "median_lead_days": 75.5, "n_positive_lead": 1}

    def test_either_head_catches_the_fin_misses(self, replay: Path) -> None:
        s = _load(replay)["summary"]
        assert s["all_either"]["n_detected"] == 5  # adds Gamma (either-only)
        assert s["all_fin"]["n_detected"] == 4


class TestRows:
    def test_positive_lead_arithmetic(self, replay: Path) -> None:
        r = _row(_load(replay), "Alpha Exchange Breach")
        assert r["lead_days_fin"] == 161.0
        assert r["n_addresses"] == 2 and r["n_in_graph"] == 2 and r["n_flagged_fin"] == 1

    def test_negative_lead_when_first_seen_after_publication(self, replay: Path) -> None:
        assert _row(_load(replay), "Delta OTC Desk")["lead_days_fin"] == -10.0

    def test_detection_takes_earliest_flagged_first_seen(self, replay: Path) -> None:
        r = _row(_load(replay), "Epsilon Laundry")
        assert r["detection_time_fin"] == "2024-01-15 00:00:00.000"
        assert r["lead_days_fin"] == 77.0

    def test_undetected_event_has_null_lead(self, replay: Path) -> None:
        r = _row(_load(replay), "Beta Bridge Exploit")
        assert r["lead_days_fin"] is None and r["detection_time_fin"] is None

    def test_address_off_graph_and_off_chain_is_counted_but_powerless(self, replay: Path) -> None:
        r = _row(_load(replay), "Gamma Drainer")
        assert r["n_addresses"] == 2
        assert r["n_in_graph"] == 1
        assert r["n_with_first_seen"] == 1
        assert r["lead_days_fin"] is None
        assert r["lead_days_either"] == 63.0  # C2, either-only

    def test_btc_tier_flagging(self, replay: Path) -> None:
        r = _row(_load(replay), "Zeta Mixer Cluster")
        assert r["n_flagged_fin"] == 1  # tier 5 yes, tier 2 no
        assert r["lead_days_fin"] == 31.0


class TestReproducibility:
    def test_utc_stamp_pins_the_output(self, replay: Path) -> None:
        data = _load(replay)
        assert data["utc"] == "2026-01-01T00:00:00Z"
        assert data["inputs"]["scores"] == "synthetic fixture"
        assert data["inputs"]["first_seen_query_ids"]["eth"] == "fixture-eth"

    def test_md_report_written(self, replay: Path) -> None:
        md = (replay.parent / "replay.md").read_text()
        assert "Static replay" in md
        assert "| ALL | 6 | 4 |" in md


class TestParseTs:
    def test_warehouse_varchar_and_iso_forms(self) -> None:
        a = leadtime.parse_ts("2024-01-01 00:00:00.000")
        b = leadtime.parse_ts("2024-01-01T00:00:00Z")
        assert a == b

    def test_naive_timestamp_becomes_utc(self) -> None:
        assert leadtime.parse_ts("2024-01-01 12:00:00").tzinfo is not None


def test_missing_input_fails_closed(world: dict) -> None:
    with pytest.raises(FileNotFoundError):
        leadtime.main(["--registry", str(world["dir"] / "nope.json"),
                       "--first-seen-dir", str(world["first_seen_dir"]),
                       "--scores", str(world["scores"]),
                       "--out-json", str(world["dir"] / "x.json"),
                       "--out-md", str(world["dir"] / "x.md")])
