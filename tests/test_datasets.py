"""Uploaded files as datasets: search, overview numbers and cases, all private to the uploader."""
import csv
import secrets
import time
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from backend.app import db, forest, search, uploads
from backend.app.main import app
from config import RESULTS_DIR

pytestmark = pytest.mark.skipif(not db.available(), reason="needs MongoDB on localhost:27017 (or MONGO_URI)")

SAMPLE = (RESULTS_DIR / "sample_upload.csv").read_bytes()
HEADER = "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud"
FRAUD_ROW = "2,TRANSFER,181.0,C1305486145,181.0,0.0,C553264065,0.0,0.0,1"
LEGIT_ROW = "14,CASH_OUT,18500,C1,42000,23500,C2,125000,143500,0"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def owner():
    return secrets.token_hex(16)


@pytest.fixture(autouse=True)
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    yield tmp_path
    for batch_id in list(uploads._dataset_connections):  # release file handles before pytest removes the folder
        uploads._close_dataset_connection(batch_id)


def h(owner):
    return {"X-Owner": owner}


def upload(client, owner, content=SAMPLE, name="data.csv"):
    r = client.post("/api/uploads", files={"file": (name, content if isinstance(content, bytes) else content.encode(), "text/csv")}, headers=h(owner))
    assert r.status_code == 202, r.text
    deadline = time.time() + 120
    while time.time() < deadline:
        batch = client.get(f"/api/uploads/{r.json()['id']}", headers=h(owner)).json()
        if batch["status"] != "processing":
            assert batch["status"] == "done", batch.get("error")
            return batch
        time.sleep(0.02)
    raise AssertionError("upload did not finish")


def scored_rows(upload_dir, batch):
    with open(upload_dir / f"{batch['id']}.csv", newline="") as f:
        return list(csv.DictReader(f))


def find(client, owner, batch, **params):
    return client.get("/api/search", params={"dataset": batch["id"], **params}, headers=h(owner)).json()


@pytest.fixture
def uploaded(client, owner):
    return upload(client, owner, name="sample.csv")


def test_the_dataset_list_has_paysim_and_only_your_uploads(client, owner, uploaded):
    items = client.get("/api/datasets", headers=h(owner)).json()["items"]
    assert [i["id"] for i in items] == ["paysim", uploaded["id"]]
    mine = items[1]
    assert mine["name"] == "sample.csv" and mine["rows"] == 500 and mine["labelled"] and mine["flagged"] == 30 and mine["kind"] == "upload"
    other = client.get("/api/datasets", headers=h(secrets.token_hex(16))).json()["items"]
    assert [i["id"] for i in other] == ["paysim"]


def test_account_search_matches_an_independent_read_of_the_scored_file(client, owner, uploaded, upload_dir):
    rows = scored_rows(upload_dir, uploaded)
    accounts = [rows[0]["nameOrig"], rows[137]["nameDest"], rows[311]["nameOrig"], next(r["nameDest"] for r in rows if r["model_flag"] == "true")]
    for account in accounts:
        expected = {int(r["row"]): r for r in rows if account in (r["nameOrig"], r["nameDest"])}
        found = find(client, owner, uploaded, account=account, limit=200)
        assert found["total"] == len(expected) and found["access"] == "index"
        assert {i["tx_id"] for i in found["items"]} == set(expected)
        for item in found["items"]:
            want = expected[item["tx_id"]]
            assert item["amount"] == pytest.approx(float(want["amount"]))
            assert (item["fraud_probability"] is None) == (want["fraud_probability"] == "")
            if want["fraud_probability"]:
                assert item["fraud_probability"] == pytest.approx(float(want["fraud_probability"]))
                assert item["model_flag"] == (want["model_flag"] == "true")
    sender = find(client, owner, uploaded, account=accounts[0], role="sender")
    assert all(i["name_orig"] == accounts[0] for i in sender["items"])


def test_stored_scores_equal_what_the_scorer_gives_for_the_same_row(client, owner, uploaded):
    flagged = find(client, owner, uploaded, flagged=True, limit=5)["items"]
    for row in flagged:
        again = forest.score_one({k: row[k] for k in ("type", "amount", "hour_of_day", "old_balance_orig", "new_balance_orig",
                                                      "old_balance_dest", "new_balance_dest")})
        assert again["fraud_probability"] == row["fraud_probability"]


def test_model_flag_and_label_filters_agree_with_the_accuracy_summary(client, owner, uploaded):
    m = uploaded["metrics"]
    total = lambda **p: find(client, owner, uploaded, limit=1, **p)["total"]
    assert total(flagged=True) == uploaded["flagged"] == m["tp"] + m["fp"]
    assert total(flagged=True, fraud=True) == m["tp"]
    assert total(flagged=True, fraud=False) == m["fp"]
    assert total(flagged=False, fraud=True) == m["fn"]
    assert total(fraud=True) == m["tp"] + m["fn"]
    assert total() == 500 and total(type="PAYMENT") + total(type="CASH_IN") == uploaded["rows_not_modelled"]


def test_sorting_by_model_score_keeps_unscored_rows_last(client, owner, uploaded, upload_dir):
    probs = sorted((float(r["fraud_probability"]) for r in scored_rows(upload_dir, uploaded) if r["fraud_probability"]), reverse=True)
    desc = find(client, owner, uploaded, sort="fraud_probability", order="desc", limit=200)["items"]
    assert desc[0]["fraud_probability"] == pytest.approx(probs[0])
    scored = [i["fraud_probability"] for i in desc if i["fraud_probability"] is not None]
    assert scored == sorted(scored, reverse=True) and len(scored) == len(desc)  # 430 scored rows fill the first 200
    asc = find(client, owner, uploaded, sort="fraud_probability", order="asc", limit=200, skip=300)["items"]
    assert any(i["fraud_probability"] is None for i in asc)  # unscored rows are at the end, not the start
    first_none = next(k for k, i in enumerate(asc) if i["fraud_probability"] is None)
    assert all(i["fraud_probability"] is None for i in asc[first_none:])


def test_paysim_search_refuses_the_upload_only_options(client):
    assert client.get("/api/search", params={"flagged": True}).status_code == 422
    assert client.get("/api/search", params={"sort": "fraud_probability"}).status_code == 422


def test_other_people_cannot_see_your_dataset(client, owner, uploaded):
    stranger = h(secrets.token_hex(16))
    for url in [f"/api/search?dataset={uploaded['id']}", f"/api/transactions/1?dataset={uploaded['id']}",
                f"/api/accounts/C1?dataset={uploaded['id']}", f"/api/datasets/{uploaded['id']}/overview"]:
        assert client.get(url, headers=stranger).status_code == 404
        assert client.get(url).status_code == 400  # no workspace id at all
    for bad in ["nope", "123", "f" * 24]:
        assert client.get(f"/api/search?dataset={bad}", headers=h(owner)).status_code == 404


def test_overview_numbers_match_independent_counts(client, owner, uploaded, upload_dir):
    rows = scored_rows(upload_dir, uploaded)
    o = client.get(f"/api/datasets/{uploaded['id']}/overview", headers=h(owner)).json()
    a = o["analytics"]
    assert o["name"] == "sample.csv" and o["rows"] == 500 and o["flagged"] == 30
    assert a["overview"]["transactions"] == 500
    assert a["overview"]["total_amount"] == pytest.approx(sum(float(r["amount"]) for r in rows), rel=1e-6)
    assert a["overview"]["scored"] == 430 and a["overview"]["flagged"] == 30 and a["overview"]["labelled_fraud"] == 30
    assert a["overview"]["senders"] == len({r["nameOrig"] for r in rows}) and a["overview"]["receivers"] == len({r["nameDest"] for r in rows})
    by_type = Counter(r["type"] for r in rows)
    assert {t["type"]: t["transactions"] for t in a["type_summary"]} == dict(by_type)
    assert sum(t["flagged"] for t in a["type_summary"]) == 30 and sum(t["labelled_fraud"] for t in a["type_summary"]) == 30
    assert sum(x["transactions"] for x in a["hourly"]) == 500 and {x["hour_of_day"] for x in a["hourly"]} == {int(r["hour_of_day"]) for r in rows}
    assert sum(x["transactions"] for x in a["amount_bands"]) == 500
    assert sum(x["transactions"] for x in a["score_histogram"]) == 430 and sum(x["labelled_fraud"] for x in a["score_histogram"]) == 30
    assert a["score_histogram"][-1]["bin"] == 9 and a["score_histogram"][-1]["transactions"] >= 30
    top = a["top_flagged_receivers"]
    assert top == sorted(top, key=lambda r: -r["total_amount"]) and sum(r["flagged"] for r in top) <= 30
    assert o["metrics"]["recall"] == uploaded["metrics"]["recall"]


def test_open_a_case_from_an_uploaded_row(client, owner, uploaded):
    row = find(client, owner, uploaded, flagged=True, sort="fraud_probability", order="desc", limit=1)["items"][0]
    created = client.post("/api/cases", json={"tx_id": row["tx_id"], "dataset": uploaded["id"], "note": "from the upload"}, headers=h(owner))
    assert created.status_code == 201
    case = created.json()
    assert case["dataset"] == uploaded["id"] and case["dataset_name"] == "sample.csv" and case["priority"] == "high"
    assert case["tx"]["name_orig"] == row["name_orig"] and case["tx"]["fraud_probability"] == row["fraud_probability"]
    # one open case per row; PaySim's transaction with the same number is a different transaction
    assert client.post("/api/cases", json={"tx_id": row["tx_id"], "dataset": uploaded["id"]}, headers=h(owner)).status_code == 409
    other = client.post("/api/cases", json={"tx_id": row["tx_id"]}, headers=h(owner))
    assert other.status_code == 201 and other.json()["dataset_name"] == "PaySim"
    # nobody else can open cases on your upload
    assert client.post("/api/cases", json={"tx_id": row["tx_id"], "dataset": uploaded["id"]}, headers=h(secrets.token_hex(16))).status_code == 404
    assert client.post("/api/cases", json={"tx_id": 99_999, "dataset": uploaded["id"]}, headers=h(owner)).status_code == 404
    assert client.post("/api/cases", json={"tx_id": 1, "dataset": "not-a-dataset"}, headers=h(owner)).status_code == 422


def test_a_case_outlives_its_upload(client, owner, uploaded):
    row = find(client, owner, uploaded, flagged=True, limit=1)["items"][0]
    case = client.post("/api/cases", json={"tx_id": row["tx_id"], "dataset": uploaded["id"]}, headers=h(owner)).json()
    assert client.delete(f"/api/uploads/{uploaded['id']}", headers=h(owner)).status_code == 204
    kept = client.get(f"/api/cases/{case['id']}", headers=h(owner)).json()
    assert kept["tx"]["name_orig"] == row["name_orig"] and kept["dataset_name"] == "sample.csv"


def test_deleting_the_upload_removes_the_dataset_and_its_files(client, owner, uploaded, upload_dir):
    assert find(client, owner, uploaded, limit=1)["total"] == 500
    assert client.delete(f"/api/uploads/{uploaded['id']}", headers=h(owner)).status_code == 204
    assert client.get(f"/api/search?dataset={uploaded['id']}", headers=h(owner)).status_code == 404
    assert [i["id"] for i in client.get("/api/datasets", headers=h(owner)).json()["items"]] == ["paysim"]
    assert not list(upload_dir.iterdir()) and uploaded["id"] not in uploads._dataset_connections


def test_a_file_with_an_hour_column_instead_of_step_can_be_explored(client, owner):
    header = "type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,hour_of_day"
    batch = upload(client, owner, "\n".join([header, "TRANSFER,181,C1,181,0,C2,0,0,3", "CASH_OUT,50,C3,100,50,C4,0,50,15"]) + "\n")
    rows = find(client, owner, batch, sort="step")["items"]
    assert {r["tx_id"]: (r["step"], r["day"], r["hour_of_day"]) for r in rows} == {1: (None, None, 3), 2: (None, None, 15)}
    o = client.get(f"/api/datasets/{batch['id']}/overview", headers=h(owner)).json()["analytics"]
    assert o["overview"]["days"] is None and {x["hour_of_day"] for x in o["hourly"]} == {3, 15}


def test_a_file_without_labels_has_no_label_numbers(client, owner):
    header = "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest"
    batch = upload(client, owner, "\n".join([header, "2,TRANSFER,181,C1,181,0,C2,0,0"]) + "\n")
    assert batch["metrics"] is None
    ds = next(i for i in client.get("/api/datasets", headers=h(owner)).json()["items"] if i["id"] == batch["id"])
    assert ds["labelled"] is False
    assert client.get(f"/api/datasets/{batch['id']}/overview", headers=h(owner)).json()["analytics"]["overview"]["labelled_fraud"] is None
    assert find(client, owner, batch, fraud=True)["total"] == 0


def test_an_expired_search_index_says_so(client, owner, uploaded, upload_dir):
    uploads._close_dataset_connection(uploaded["id"])
    (upload_dir / f"{uploaded['id']}.duckdb").unlink()
    r = client.get(f"/api/search?dataset={uploaded['id']}", headers=h(owner))
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    assert [i["id"] for i in client.get("/api/datasets", headers=h(owner)).json()["items"]] == ["paysim"]


def test_more_datasets_than_open_connections_still_work(client, owner, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_OPEN_DATASETS", 1)
    a = upload(client, owner, SAMPLE, "a.csv")
    b = upload(client, owner, "\n".join([HEADER, FRAUD_ROW, LEGIT_ROW]) + "\n", "b.csv")
    for _ in range(2):  # alternating forces the single cached connection to be replaced each time
        assert find(client, owner, a, limit=1)["total"] == 500
        assert find(client, owner, b, limit=1)["total"] == 2
    assert len(uploads._dataset_connections) == 1


def test_transaction_and_account_views_work_for_uploads(client, owner, uploaded):
    row = find(client, owner, uploaded, flagged=True, limit=1)["items"][0]
    one = client.get(f"/api/transactions/{row['tx_id']}", params={"dataset": uploaded["id"]}, headers=h(owner)).json()
    assert one["tx_id"] == row["tx_id"] and one["fraud_probability"] == row["fraud_probability"]
    summary = client.get(f"/api/accounts/{row['name_orig']}", params={"dataset": uploaded["id"]}, headers=h(owner)).json()
    assert summary["as_sender"]["transactions"] >= 1
    assert client.get("/api/transactions/9999", params={"dataset": uploaded["id"]}, headers=h(owner)).status_code == 404


def test_a_large_upload_is_searchable_and_summarised(client, owner):
    rows = [(FRAUD_ROW.replace("C1305486145", f"C{i}") if i % 40 == 0 else LEGIT_ROW.replace(",C1,", f",C{i},")) for i in range(300_000)]
    batch = upload(client, owner, "\n".join([HEADER, *rows]) + "\n", "large.csv")
    assert batch["rows_valid"] == 300_000 and batch["indexing_ms"] > 0
    found = find(client, owner, batch, account="C80", role="sender")
    assert found["total"] == 1 and found["items"][0]["model_flag"] is True and found["took_ms"] < 100
    a = client.get(f"/api/datasets/{batch['id']}/overview", headers=h(owner)).json()["analytics"]
    assert a["overview"]["transactions"] == 300_000 and a["overview"]["flagged"] == batch["flagged"] == 7500


def test_cases_can_be_filtered_by_dataset(client, owner, uploaded):
    row = find(client, owner, uploaded, flagged=True, limit=1)["items"][0]
    mine = client.post("/api/cases", json={"tx_id": row["tx_id"], "dataset": uploaded["id"]}, headers=h(owner)).json()
    paysim = client.post("/api/cases", json={"tx_id": 425}, headers=h(owner)).json()
    ids = lambda **p: {c["id"] for c in client.get("/api/cases", params=p, headers=h(owner)).json()["items"]}
    assert ids() == ids(dataset="all") == {mine["id"], paysim["id"]}
    assert ids(dataset=uploaded["id"]) == {mine["id"]} and ids(dataset="paysim") == {paysim["id"]}
    stats = client.get("/api/cases/stats", params={"dataset": uploaded["id"]}, headers=h(owner)).json()
    assert stats["total"] == 1 and stats["total_all_datasets"] == 2
    assert client.get("/api/cases/stats", params={"dataset": "paysim"}, headers=h(owner)).json()["total"] == 1
    assert client.get("/api/cases", params={"dataset": "nonsense"}, headers=h(owner)).status_code == 422
    # filters combine: a search inside one dataset
    assert ids(dataset=uploaded["id"], q=row["name_orig"]) == {mine["id"]} and ids(dataset="paysim", q=row["name_orig"]) == set()


def test_cases_made_before_datasets_existed_belong_to_paysim(client, owner):
    from bson import ObjectId

    cases = db.collection("cases")
    oid = ObjectId()
    now = uploads._now()
    cases.insert_one({"_id": oid, "owner": owner, "tx_ref": "dataset:7", "tx_id": 7, "tx": {"name_orig": "C1", "name_dest": "C2"},
                      "status": "new", "is_open": True, "priority": "low", "assignee": "", "notes": [], "history": [],
                      "created_at": now, "updated_at": now})
    listed = client.get("/api/cases", params={"dataset": "paysim"}, headers=h(owner)).json()["items"]
    assert [c["id"] for c in listed] == [str(oid)]
