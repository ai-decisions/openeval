"""Synthetic fixtures for the openeval pipeline — executable schema docs.

Every fixture is built in tmp_path from the literals below; nothing is
read from outside the test run. The synthetic world, so the expected
numbers in the tests can be checked by hand:

  Alpha Exchange Breach (ethereum, scraped, pub 2024-06-10)
      A1 flagged fin+either, first seen 2024-01-01 -> lead 161.0
      A2 in graph, not flagged
  Beta Bridge Exploit   (ethereum, proxy, sentinel event_date 2020-01-01)
      B1 in graph, not flagged -> undetected; date audit resolves the date
  Gamma Drainer         (ethereum, fallback, sentinel event_date)
      C1 NOT in graph and not on chain; C2 either-only, first seen
      2024-05-01 -> fin-undetected, either lead 63.0; audit UNRESOLVED
  Delta OTC Desk        (tron, scraped, pub 2024-03-01)
      D1 flagged, first seen 2024-03-11 -> lead -10.0 (behind publication)
  Epsilon Laundry       (tron, proxy, pub 2024-04-01)
      E5A flagged fs 2024-02-01; E5B flagged fs 2024-01-15 -> detection
      takes the EARLIEST flagged first-seen -> lead 77.0
  Zeta Mixer Cluster    (bitcoin, proxy, pub 2024-01-01)
      F1 tier 5 (flagged), first seen 2023-12-01 -> lead 31.0; F2 tier 2

Non-EVM events deliberately carry proxy dates only — the event-lift
cross-check enforces exactly that convention.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

A1 = "0x" + "a1" * 20
A2 = "0x" + "a2" * 20
B1 = "0x" + "b1" * 20
C1 = "0x" + "c1" * 20
C2 = "0x" + "c2" * 20
D1 = "0x" + "d1" * 20
E5A = "0x" + "e1" * 20
E5B = "0x" + "e2" * 20
F1 = "1SynthBtcAddrTierFiveAaaa1"
F2 = "1SynthBtcAddrTierTwoBbbb22"

EVENTS = [
    {"name": "Alpha Exchange Breach", "chain": "ethereum", "event_date": "2024-01-05",
     "publication_date": "2024-06-10", "publication_source": "scraped",
     "category": "exchange breach",
     "addresses": [{"address": A1}, {"address": A2}]},
    {"name": "Beta Bridge Exploit", "chain": "ethereum", "event_date": "2020-01-01",
     "publication_date": "2024-04-04", "publication_source": "event_date_proxy_+3d",
     "category": "bridge exploit",
     "addresses": [{"address": B1}]},
    {"name": "Gamma Drainer", "chain": "ethereum", "event_date": "2020-01-01",
     "publication_date": "2024-07-03", "publication_source": "event_date_fallback",
     "category": "drainer",
     "addresses": [{"address": C1}, {"address": C2}]},
    {"name": "Delta OTC Desk", "chain": "tron", "event_date": "2024-02-20",
     "publication_date": "2024-03-01", "publication_source": "scraped",
     "category": "sanctions designation",
     "addresses": [{"address": D1}]},
    {"name": "Epsilon Laundry", "chain": "tron", "event_date": "2024-01-10",
     "publication_date": "2024-04-01", "publication_source": "event_date_proxy_+3d",
     "category": "laundering network",
     "addresses": [{"address": E5A}, {"address": E5B}]},
    {"name": "Zeta Mixer Cluster", "chain": "bitcoin", "event_date": "2023-11-25",
     "publication_date": "2024-01-01", "publication_source": "event_date_proxy_+3d",
     "category": "mixer",
     "addresses": [{"address": F1}, {"address": F2}]},
]

def _fs(ts: str) -> dict:
    return {"first_seen": ts, "n_activity": 3}


FIRST_SEEN = {
    "eth": {A1: _fs("2024-01-01 00:00:00.000"), A2: _fs("2024-02-01 00:00:00.000"),
            B1: _fs("2024-03-01 00:00:00.000"), C2: _fs("2024-05-01 00:00:00.000")},
    "tron": {D1: _fs("2024-03-11 00:00:00.000"), E5A: _fs("2024-02-01 00:00:00.000"),
             E5B: _fs("2024-01-15 00:00:00.000")},
    "btc": {F1: _fs("2023-12-01 00:00:00.000"), F2: _fs("2023-10-01 00:00:00.000")},
}


def _evm_row(addr: str, fin: bool, either: bool, score: float) -> dict:
    return {"address": addr, "in_graph": True, "s_fincrime": score,
            "flagged_fincrime": {"0.001": fin}, "flagged_either": {"0.001": either}}


SCORES = {
    "chains": {
        "eth": [
            _evm_row(A1, True, True, 0.91),
            _evm_row(A2, False, False, 0.40),
            _evm_row(B1, False, False, 0.10),
            _evm_row(C2, False, True, 0.55),
        ],
        "tron": [
            _evm_row(D1, True, True, 0.88),
            _evm_row(E5A, True, True, 0.93),
            _evm_row(E5B, True, True, 0.95),
        ],
        "btc": [
            {"address": F1, "in_graph": True, "score": 0.999, "risk_tier": 5},
            {"address": F2, "in_graph": True, "score": 0.5, "risk_tier": 2},
        ],
    }
}

PATCH = {
    "Beta Bridge Exploit": {"date": "2024-05-05", "scope": "test scope",
                            "source": "https://example.org/beta"},
    "Gamma Drainer": {"date": "UNRESOLVED"},
}


def build_world(root: Path) -> dict:
    """Write the synthetic inputs under `root` and return their paths."""
    root.mkdir(parents=True, exist_ok=True)
    registry = root / "registry.json"
    registry.write_text(json.dumps(EVENTS, indent=1))
    fs_dir = root / "first_seen"
    fs_dir.mkdir(exist_ok=True)
    for chain, addrs in FIRST_SEEN.items():
        (fs_dir / f"first_seen_{chain}.json").write_text(
            json.dumps({"query_id": f"fixture-{chain}", "addresses": addrs}))
    scores = root / "scores.json"
    scores.write_text(json.dumps(SCORES))
    patch = root / "patch.json"
    patch.write_text(json.dumps(PATCH))
    return {"registry": registry, "first_seen_dir": fs_dir, "scores": scores,
            "patch": patch, "dir": root}


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return build_world(tmp_path)


def run_leadtime(world: dict, **extra: str) -> Path:
    from openeval import leadtime

    out = world["dir"] / "replay.json"
    argv = ["--registry", str(world["registry"]),
            "--first-seen-dir", str(world["first_seen_dir"]),
            "--scores", str(world["scores"]),
            "--out-json", str(out),
            "--out-md", str(world["dir"] / "replay.md"),
            "--utc-stamp", "2026-01-01T00:00:00Z",
            "--scores-note", "synthetic fixture"]
    for k, v in extra.items():
        argv += [f"--{k.replace('_', '-')}", v]
    leadtime.main(argv)
    return out


def run_date_audit(world: dict, replay: Path) -> Path:
    from openeval import date_audit

    out = world["dir"] / "replay_audited.json"
    date_audit.main(["--replay", str(replay),
                     "--patch", str(world["patch"]),
                     "--out-json", str(out),
                     "--out-md", str(world["dir"] / "replay_audited.md"),
                     "--utc-stamp", "2026-01-01T00:00:00Z"])
    return out


@pytest.fixture
def replay(world: dict) -> Path:
    return run_leadtime(world)


@pytest.fixture
def audited(world: dict, replay: Path) -> Path:
    return run_date_audit(world, replay)


def load_script(name: str):
    """Import a scripts/*.py runner by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
