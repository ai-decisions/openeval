"""scripts/athena_first_seen.py — template rendering, validation, pagination.

The Athena SDK is faked; no test talks to a network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.conftest import load_script

athena = load_script("athena_first_seen")

GOOD_ETH = ["0x" + "ab" * 20, "0x" + "cd" * 20]
GOOD_BTC = ["1SynthBtcAddrTierFiveAaaa1", "bc1qsyntestaddr0000zzzzz"]


class TestAddressLoading:
    def test_plain_list_and_json_list_both_load(self, tmp_path: Path) -> None:
        plain = tmp_path / "a.txt"
        plain.write_text("# comment\n" + "\n".join(GOOD_ETH) + "\n\n")
        as_json = tmp_path / "a.json"
        as_json.write_text(json.dumps(GOOD_ETH))
        assert athena.load_addresses(plain, "eth") == sorted(GOOD_ETH)
        assert athena.load_addresses(as_json, "eth") == sorted(GOOD_ETH)

    def test_duplicates_collapse(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("\n".join(GOOD_ETH + GOOD_ETH))
        assert athena.load_addresses(f, "eth") == sorted(GOOD_ETH)

    def test_sql_injection_shaped_address_is_fatal(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text(GOOD_ETH[0] + "\n0xdead'); DROP TABLE x;--\n")
        with pytest.raises(SystemExit, match="format check"):
            athena.load_addresses(f, "eth")

    def test_uppercase_hex_is_rejected_not_normalised(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("0x" + "AB" * 20)
        with pytest.raises(SystemExit, match="format check"):
            athena.load_addresses(f, "eth")

    def test_btc_charset(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("\n".join(GOOD_BTC))
        assert athena.load_addresses(f, "btc") == sorted(GOOD_BTC)
        f.write_text("1ContainsZero0Illegal000000")
        with pytest.raises(SystemExit, match="format check"):
            athena.load_addresses(f, "btc")

    def test_empty_file_is_fatal(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("# nothing\n")
        with pytest.raises(SystemExit, match="empty"):
            athena.load_addresses(f, "eth")

    def test_trailing_newline_inside_a_json_value_is_fatal(self, tmp_path: Path) -> None:
        # `$` alone matches before a trailing newline; the check must not.
        f = tmp_path / "a.json"
        f.write_text(json.dumps([GOOD_ETH[0] + "\n"]))
        with pytest.raises(SystemExit, match="format check"):
            athena.load_addresses(f, "eth")

    def test_non_string_json_entry_is_fatal_not_a_stacktrace(self, tmp_path: Path) -> None:
        f = tmp_path / "a.json"
        f.write_text(json.dumps([GOOD_ETH[0], {"address": GOOD_ETH[1]}]))
        with pytest.raises(SystemExit, match="format check"):
            athena.load_addresses(f, "eth")


class TestRendering:
    @pytest.mark.parametrize("chain", ["eth", "tron", "btc"])
    def test_every_shipped_template_renders_structurally_valid_sql(self, chain: str) -> None:
        """Substring checks are not enough: a placeholder quoted in a header
        comment once injected bare VALUES rows mid-comment, and every
        substring assertion still passed. Address rows must sit ONLY inside
        the VALUES block, and each address exactly once in the whole SQL."""
        addrs = GOOD_BTC if chain == "btc" else GOOD_ETH
        sql = athena.render(chain, addrs, "my_database", athena.TEMPLATES_DIR)
        assert "{{" not in sql
        assert "my_database." in sql
        lines = sql.splitlines()
        open_i = next(i for i, line in enumerate(lines) if line.rstrip().endswith("(VALUES"))
        close_i = next(i for i, line in enumerate(lines) if ") AS t(address)" in line)
        for a in addrs:
            assert sql.count(f"('{a}')") == 1
        for i, line in enumerate(lines):
            if "('" in line:
                assert open_i < i < close_i, f"address row outside the VALUES block: {line!r}"
            if line.lstrip().startswith("--"):
                assert "('" not in line

    def test_template_with_placeholder_quoted_elsewhere_is_fatal(self, tmp_path: Path) -> None:
        """Regression for the comment-injection defect: a template carrying
        the address placeholder twice must be refused, not rendered."""
        (tmp_path / "first_seen_eth.sql.tmpl").write_text(
            "-- header quoting {{ADDRESS_VALUES}}\n"
            "WITH a AS (SELECT address FROM (VALUES\n{{ADDRESS_VALUES}}\n"
            ") AS t(address)) SELECT 1 FROM {{DATABASE}}.t")
        with pytest.raises(SystemExit, match="exactly once"):
            athena.render("eth", GOOD_ETH, "db", tmp_path)

    def test_template_without_database_placeholder_is_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "first_seen_eth.sql.tmpl").write_text(
            "WITH a AS (SELECT address FROM (VALUES\n{{ADDRESS_VALUES}}\n"
            ") AS t(address)) SELECT 1 FROM hardcoded.t")
        with pytest.raises(SystemExit, match="no DATABASE placeholder"):
            athena.render("eth", GOOD_ETH, "db", tmp_path)

    def test_unrendered_placeholder_is_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "first_seen_eth.sql.tmpl").write_text(
            "SELECT 1 FROM {{DATABASE}}.t WHERE x IN ({{ADDRESS_VALUES}}) -- {{TYPO}}")
        with pytest.raises(SystemExit, match="unrendered placeholder"):
            athena.render("eth", GOOD_ETH, "db", tmp_path)


class FakeAthena:
    """Two-page result set; asserts the workgroup is the one requested."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def start_query_execution(self, QueryString: str, WorkGroup: str) -> dict:
        assert WorkGroup == "test-wg"
        self.calls.append("start")
        return {"QueryExecutionId": "q-1"}

    def get_query_execution(self, QueryExecutionId: str) -> dict:
        return {"QueryExecution": {
            "Status": {"State": "SUCCEEDED"},
            "Statistics": {"DataScannedInBytes": 12_000_000_000,
                           "TotalExecutionTimeInMillis": 4200}}}

    def get_query_results(self, QueryExecutionId: str, MaxResults: int,
                          NextToken: str | None = None) -> dict:
        def row(*vals: str) -> dict:
            return {"Data": [{"VarCharValue": v} for v in vals]}
        if NextToken is None:
            return {"ResultSet": {"Rows": [
                row("address", "first_seen", "n_activity"),
                row(GOOD_ETH[0], "2024-01-01 00:00:00.000", "7"),
            ]}, "NextToken": "page-2"}
        return {"ResultSet": {"Rows": [
            row(GOOD_ETH[1], "2024-02-01 00:00:00.000", "3"),
        ]}}


class FakeBoto3:
    def __init__(self, client_obj: FakeAthena) -> None:
        self._client = client_obj

    def client(self, service: str, region_name: str):
        assert service == "athena" and region_name == "eu-test-1"
        return self._client


def test_main_paginates_fully_and_writes_the_leadtime_shape(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3(FakeAthena()))
    addr_file = tmp_path / "a.txt"
    addr_file.write_text("\n".join(GOOD_ETH))
    out = tmp_path / "first_seen_eth.json"
    athena.main(["--chain", "eth", "--addresses", str(addr_file),
                 "--database", "db", "--workgroup", "test-wg",
                 "--region", "eu-test-1", "--out", str(out),
                 "--predicted-bytes", "11000000000"])
    data = json.loads(out.read_text())
    assert data["query_id"] == "q-1"
    assert data["n_addresses_found"] == 2  # both pages landed
    assert data["addresses"][GOOD_ETH[1]] == {
        "first_seen": "2024-02-01 00:00:00.000", "n_activity": 3}
    assert data["scanned_bytes"] == 12_000_000_000
    assert data["cost_usd"] == round(12_000_000_000 / 1e12 * 5.0, 4)


def test_warehouse_configuration_is_fail_closed(tmp_path: Path) -> None:
    addr_file = tmp_path / "a.txt"
    addr_file.write_text(GOOD_ETH[0])
    with pytest.raises(SystemExit):  # argparse: --database et al. required
        athena.main(["--chain", "eth", "--addresses", str(addr_file)])
