import json

import pyarrow.dataset as ds
import pytest
from fastapi.testclient import TestClient

from backend.app import search
from backend.app.main import app
from config import LAKE_DIR, RESULTS_DIR, SEARCH_DB

pytestmark = pytest.mark.skipif(
    not (SEARCH_DB.exists() and LAKE_DIR.exists()),
    reason="needs the search index and the lake (python run_pipeline.py)")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def lake():
    return ds.dataset(str(LAKE_DIR), format="parquet", partitioning="hive")


def accounts_at(tx_ids):
    cur = search._connection().cursor()
    out = []
    for tx_id in tx_ids:
        out += cur.execute("SELECT name_orig, name_dest FROM transactions WHERE tx_id = ?", [tx_id]).fetchone()
    return sorted(set(out))


SAMPLE = accounts_at([1, 777, 50_000, 1_234_567, 3_000_000, 5_555_555, 6_362_000])


@pytest.mark.parametrize("account", SAMPLE)
def test_any_role_matches_a_pyarrow_scan_of_the_lake(client, lake, account):
    expected = lake.to_table(
        columns=["step", "amount", "name_orig", "name_dest"],
        filter=(ds.field("name_orig") == account) | (ds.field("name_dest") == account)).to_pylist()
    r = client.get("/api/search", params={"account": account, "limit": 200}).json()
    assert r["total"] == len(expected)
    assert sorted((i["step"], i["amount"]) for i in r["items"]) == sorted((e["step"], e["amount"]) for e in expected)
    assert r["access"] == "index"


@pytest.mark.parametrize("role,column", [("sender", "name_orig"), ("receiver", "name_dest")])
def test_roles_are_respected(client, lake, role, column):
    account = SAMPLE[3]
    expected = lake.to_table(columns=["amount"], filter=ds.field(column) == account).num_rows
    r = client.get("/api/search", params={"account": account, "role": role}).json()
    assert r["total"] == expected
    assert all(i[column] == account for i in r["items"])


def test_filters_combine_with_an_account(client):
    cur = search._connection().cursor()
    account = cur.execute("SELECT name_dest FROM transactions WHERE is_fraud = 1 LIMIT 1").fetchone()[0]
    r = client.get("/api/search", params={"account": account, "fraud": True}).json()
    assert r["total"] >= 1 and all(i["is_fraud"] == 1 for i in r["items"])


def test_fraud_filter_totals_match_the_analytics_results(client):
    by_type = {t["type"]: t["fraud_transactions"] for t in json.loads((RESULTS_DIR / "type_summary.json").read_text())}
    r = client.get("/api/search", params={"type": "TRANSFER", "fraud": True, "limit": 1}).json()
    assert r["total"] == by_type["TRANSFER"] and r["access"] == "scan"
    assert client.get("/api/search", params={"limit": 1}).json()["total"] == 6_362_604


def test_sorting_and_paging(client):
    params = {"type": "TRANSFER", "sort": "amount", "order": "desc", "limit": 10}
    first = client.get("/api/search", params=params).json()["items"]
    second = client.get("/api/search", params={**params, "skip": 10}).json()["items"]
    amounts = [i["amount"] for i in first + second]
    assert amounts == sorted(amounts, reverse=True)
    assert not {i["tx_id"] for i in first} & {i["tx_id"] for i in second}


def test_sql_injection_is_rejected_or_harmless(client):
    before = client.get("/api/search", params={"limit": 1}).json()["total"]
    for payload in ["C1'; DROP TABLE transactions; --", "x' OR '1'='1", "C1) OR (1=1", "a" * 40]:
        assert client.get("/api/search", params={"account": payload}).status_code == 422
    assert client.get("/api/search", params={"sort": "amount; DROP TABLE transactions"}).status_code == 422
    assert client.get("/api/search", params={"limit": 1}).json()["total"] == before


@pytest.mark.parametrize("params", [
    {"hour_from": 9, "hour_to": 3}, {"min_amount": 10, "max_amount": 5}, {"limit": 500}, {"limit": 0},
    {"skip": -1}, {"hour_to": 24}, {"type": "WIRE"}, {"role": "owner"},
])
def test_invalid_parameters_are_rejected(client, params):
    assert client.get("/api/search", params=params).status_code == 422


def test_unknown_account_returns_no_rows_not_an_error(client):
    r = client.get("/api/search", params={"account": "C000000000000"}).json()
    assert r["total"] == 0 and r["items"] == []


def test_scores_only_the_types_the_model_was_trained_on(client):
    items = client.get("/api/search", params={"limit": 200}).json()["items"]
    for i in items:
        if i["type"] in ("TRANSFER", "CASH_OUT"):
            assert 0 <= i["fraud_probability"] <= 1 and isinstance(i["model_flag"], bool)
        else:
            assert i["fraud_probability"] is None and i["model_flag"] is None


def test_transaction_detail_and_account_summary(client):
    row = client.get("/api/search", params={"account": SAMPLE[0], "limit": 1}).json()["items"][0]
    assert client.get(f"/api/transactions/{row['tx_id']}").json()["tx_id"] == row["tx_id"]
    assert client.get("/api/transactions/0").status_code == 404
    summary = client.get(f"/api/accounts/{SAMPLE[0]}").json()
    assert summary["as_sender"]["transactions"] + summary["as_receiver"]["transactions"] >= 1
    assert client.get("/api/accounts/C000000000000").status_code == 404
    assert client.get("/api/accounts/a'b").status_code == 422


def test_tx_ids_are_deterministic_across_rebuilds(client):
    # tx_id is row_number() over a total order of every column, so it must follow that order
    cur = search._connection().cursor()
    a, b = cur.execute("SELECT step, name_orig FROM transactions WHERE tx_id IN (1, 6362604) ORDER BY tx_id").fetchall()
    assert a[0] <= b[0]
    assert cur.execute("SELECT count(DISTINCT tx_id) = count(*) FROM transactions").fetchone()[0]


def test_account_lookup_is_fast_once_warm(client):
    client.get("/api/search", params={"account": SAMPLE[1]})
    took = [client.get("/api/search", params={"account": a, "role": "sender"}).json()["took_ms"] for a in SAMPLE]
    assert sorted(took)[len(took) // 2] < 50  # measured ~6 ms; generous bound so a busy machine doesn't fail it


def test_health_reports_the_index(client):
    assert client.get("/api/health").json()["search_index"]["rows"] == 6_362_604
