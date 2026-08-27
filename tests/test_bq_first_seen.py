"""scripts/bq_first_seen.py — query construction and validation (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import load_script

bq = load_script("bq_first_seen")

GOOD = ["0x" + "ab" * 20, "0x" + "cd" * 20]


class TestAddressLoading:
    def test_lowercases_and_dedups(self, tmp_path: Path) -> None:
        f = tmp_path / "a.json"
        f.write_text(json.dumps(["0x" + "AB" * 20, "0x" + "ab" * 20]))
        assert bq.load_addresses(f) == ["0x" + "ab" * 20]

    def test_injection_shaped_address_is_fatal(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text(GOOD[0] + "\n0x' OR 1=1 --aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        with pytest.raises(SystemExit, match="format check"):
            bq.load_addresses(f)

    def test_empty_is_fatal(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("\n")
        with pytest.raises(SystemExit, match="empty"):
            bq.load_addresses(f)

    def test_trailing_newline_inside_a_json_value_is_fatal(self, tmp_path: Path) -> None:
        f = tmp_path / "a.json"
        f.write_text(json.dumps([GOOD[0] + "\n"]))
        with pytest.raises(SystemExit, match="format check"):
            bq.load_addresses(f)

    def test_non_string_json_entry_is_fatal_not_a_stacktrace(self, tmp_path: Path) -> None:
        f = tmp_path / "a.json"
        f.write_text(json.dumps([GOOD[0], 42]))
        with pytest.raises(SystemExit, match="format check"):
            bq.load_addresses(f)


class TestQueryConstruction:
    def test_ethereum_unions_transactions_and_traces_from_and_to(self) -> None:
        q = bq.build_query("ethereum", GOOD, None)
        assert q.count("UNION ALL") == 3  # 4 selects
        assert q.count("crypto_ethereum.transactions") == 2
        assert q.count("crypto_ethereum.traces") == 2
        for a in GOOD:
            assert f"'{a}'" in q

    def test_tron_uses_the_public_tron_dataset(self) -> None:
        q = bq.build_query("tron", GOOD, None)
        assert q.count("UNION ALL") == 1  # 2 selects
        assert "goog_blockchain_tron_mainnet_us.transactions" in q

    def test_start_date_is_opt_in(self) -> None:
        assert "block_timestamp >=" not in bq.build_query("ethereum", GOOD, None)
        q = bq.build_query("ethereum", GOOD, "2020-01-01")
        # one bound per SELECT arm
        assert q.count("block_timestamp >= '2020-01-01'") == 4

    def test_aggregates_min_max_count(self) -> None:
        q = bq.build_query("tron", GOOD, None)
        assert "MIN(block_timestamp) AS first_seen" in q
        assert "MAX(block_timestamp) AS last_seen" in q
        assert "COUNT(*) AS n_activity" in q


def test_project_is_fail_closed(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text(GOOD[0])
    with pytest.raises(SystemExit):  # argparse: --project required
        bq.main(["--chain", "ethereum", "--addresses", str(f),
                 "--out", str(tmp_path / "o.json")])
