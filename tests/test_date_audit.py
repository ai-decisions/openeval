"""openeval.date_audit — sentinel-dated events get audited dates, leads recompute."""

from __future__ import annotations

import json
from pathlib import Path


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def _row(data: dict, name: str) -> dict:
    return next(r for r in data["events"] if r["name"] == name)


def test_resolved_patch_replaces_date_and_source(audited: Path) -> None:
    r = _row(_load(audited), "Beta Bridge Exploit")
    assert r["publication_date"] == "2024-05-05"
    assert r["publication_source"] == "ofac_scraped_d7"
    assert r["audited_source_url"] == "https://example.org/beta"


def test_unresolved_patch_keeps_the_proxy_date(audited: Path) -> None:
    data = _load(audited)
    r = _row(data, "Gamma Drainer")
    assert r["publication_date"] == "2024-07-03"
    assert r["publication_source"] == "event_date_fallback"
    assert data["summary"]["date_audit"]["unresolved"] == ["Gamma Drainer"]
    assert data["summary"]["date_audit"]["patched_events"] == 1


def test_non_sentinel_events_are_untouched(audited: Path, replay: Path) -> None:
    before = _row(_load(replay), "Alpha Exchange Breach")
    after = _row(_load(audited), "Alpha Exchange Breach")
    assert before["publication_date"] == after["publication_date"]
    assert before["lead_days_fin"] == after["lead_days_fin"]


def test_detection_counts_are_date_independent(audited: Path, replay: Path) -> None:
    b, a = _load(replay)["summary"], _load(audited)["summary"]
    for chain in ("ethereum", "tron", "bitcoin"):
        assert b["per_chain_fin"][chain]["n_events"] == a["per_chain_fin"][chain]["n_events"]
        assert b["per_chain_fin"][chain]["n_detected"] == a["per_chain_fin"][chain]["n_detected"]


def test_patched_source_counts_as_scraped(audited: Path) -> None:
    # scraped grows: Alpha + Delta + audited Beta (undetected) = 3 events / 2 detected
    s = _load(audited)["summary"]["scraped_only_fin"]
    assert s["n_events"] == 3 and s["n_detected"] == 2


def test_input_provenance_recorded_with_shas(audited: Path) -> None:
    inputs = _load(audited)["inputs"]
    assert len(inputs["original_sha256"]) == 64
    assert len(inputs["patch_sha256"]) == 64
