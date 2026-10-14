import csv
import io
import os
import secrets
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import db, uploads
from backend.app.main import app
from config import RESULTS_DIR

pytestmark = pytest.mark.skipif(not db.available(), reason="needs MongoDB on localhost:27017 (or MONGO_URI)")

SAMPLE = (RESULTS_DIR / "sample_upload.csv").read_bytes()
HEADER = "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud"
FRAUD_ROW = "2,TRANSFER,181.0,C1305486145,181.0,0.0,C553264065,0.0,0.0,1"
LEGIT_ROW = "14,CASH_OUT,18500,C1,42000,23500,C2,125000,143500,0"
WIRE_ROW = "3,WIRE,10,C1,10,0,C2,0,0,0"


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
    return tmp_path


def h(owner):
    return {"X-Owner": owner}


class Finished:
    """Stands in for a response once a background job is done: 201 with the batch, or 422 with its error."""

    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


def wait_for_job(client, owner, batch_id, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        batch = client.get(f"/api/uploads/{batch_id}", headers=h(owner)).json()
        if batch["status"] != "processing":
            return batch
        time.sleep(0.02)
    raise AssertionError("the upload job did not finish")


def post(client, owner, content, name="t.csv", wait=True):
    """Uploads and, by default, waits for the job. Files rejected up front come back as the 4xx response."""
    if isinstance(content, str):
        content = content.encode()
    r = client.post("/api/uploads", files={"file": (name, content, "text/csv")}, headers=h(owner))
    if r.status_code != 202 or not wait:
        return r
    batch = wait_for_job(client, owner, r.json()["id"])
    return Finished(422, {"detail": batch["error"]}) if batch["status"] == "failed" else Finished(201, batch)


def make(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


def test_sample_file_is_scored_and_summarised(client, owner):
    r = post(client, owner, SAMPLE, "sample.csv")
    assert r.status_code == 201
    b = r.json()
    assert (b["rows_total"], b["rows_valid"], b["rows_invalid"]) == (500, 500, 0)
    assert b["rows_scored"] == 239 and b["rows_not_modelled"] == 261  # TRANSFER and CASH_OUT only
    assert b["metrics"]["recall"] > 0.9 and b["metrics"]["precision"] > 0.9
    assert b["metrics"]["rows"] == 239 and sum(b["metrics"][k] for k in ("tp", "fp", "fn", "tn")) == 239
    probs = [x["fraud_probability"] for x in b["flagged_rows"]]
    assert probs == sorted(probs, reverse=True) and all(p > 0.5 for p in probs)
    assert b["flagged"] == len(b["flagged_rows"]) and "owner" not in b
    assert b["download_available"] and b["ignored_columns"] == []


def test_scores_agree_with_the_predict_endpoint(client, owner):
    row = post(client, owner, SAMPLE).json()["flagged_rows"][0]
    single = client.post("/api/predict", json={
        "type": row["type"], "amount": row["amount"], "hour_of_day": row["hour_of_day"],
        "old_balance_orig": row["old_balance_orig"], "new_balance_orig": row["new_balance_orig"],
        "old_balance_dest": row["old_balance_dest"], "new_balance_dest": row["new_balance_dest"]}).json()
    assert single["fraud_probability"] == row["fraud_probability"]


def test_download_contains_every_valid_row(client, owner):
    b = post(client, owner, SAMPLE).json()
    r = client.get(f"/api/uploads/{b['id']}/scored.csv", headers=h(owner))
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 500 and rows[0]["row"] == "1"
    modelled = [x for x in rows if x["type"] in ("TRANSFER", "CASH_OUT")]
    assert all(x["fraud_probability"] != "" for x in modelled)
    other = [x for x in rows if x["type"] not in ("TRANSFER", "CASH_OUT")]
    assert all(x["fraud_probability"] == "" and x["model_flag"] == "" for x in other)


def test_header_spellings_bom_and_extra_columns(client, owner):
    text = ("﻿TYPE,Amount,name_orig,old_balance_orig,new_balance_orig,name_dest,old_balance_dest,"
            "new_balance_dest,hour_of_day,notes\nTRANSFER,181,C1,181,0,C2,0,0,1,hello\n")
    b = post(client, owner, text).json()
    assert b["rows_valid"] == 1 and b["ignored_columns"] == ["notes"] and b["metrics"] is None
    assert b["flagged_rows"][0]["hour_of_day"] == 1


def test_a_file_without_labels_has_no_metrics(client, owner):
    header = "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest"
    b = post(client, owner, make("2,TRANSFER,181,C1,181,0,C2,0,0", header=header)).json()
    assert b["metrics"] is None and b["flagged"] == 1


@pytest.mark.parametrize("header,expect", [
    ("type,amount", "nameOrig"),
    ("step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest", "newbalanceDest"),
    ("type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest", "step (or hour_of_day)"),
])
def test_missing_columns_are_named(client, owner, header, expect):
    r = post(client, owner, header + "\n" + ",".join(["1"] * len(header.split(","))) + "\n")
    assert r.status_code == 422 and expect in r.json()["detail"] and "Expected a header like" in r.json()["detail"]


def test_bad_rows_are_reported_not_fatal(client, owner):
    bad = make(
        FRAUD_ROW,
        WIRE_ROW,
        "3,TRANSFER,-5,C1,10,0,C2,0,0,0",
        "3,TRANSFER,abc,C1,10,0,C2,0,0,0",
        "3,TRANSFER,10,C1,-1,0,C2,0,0,0",
        "3,TRANSFER,10,,10,0,C2,0,0,0",
        "0,TRANSFER,10,C1,10,0,C2,0,0,0",
        "3,TRANSFER,10,C1,10,0,C2,0,0,2",
        "3,TRANSFER,10,C1,10",
        LEGIT_ROW)
    b = post(client, owner, bad).json()
    assert b["rows_valid"] == 2 and b["rows_invalid"] == 8 and b["rows_total"] == 10
    messages = " | ".join(e["message"] for e in b["errors"])
    for part in ("type 'WIRE'", "amount must be greater than 0", "amount is empty or not a number",
                 "balances must not be negative", "name_orig must be 1-32 characters",
                 "step must be a whole number", "isFraud must be 0 or 1", "wrong number of columns"):
        assert part in messages
    assert [e["row"] for e in b["errors"]] == sorted(e["row"] for e in b["errors"])


def test_row_numbers_match_the_file_even_after_malformed_lines(client, owner):
    # file rows: 1 ok, 2 ragged, 3 bad type, 4 ok, 5 ragged, 6 ok
    ragged = "3,TRANSFER,10,C1,10"
    b = post(client, owner, make(LEGIT_ROW, ragged, WIRE_ROW, FRAUD_ROW, ragged, LEGIT_ROW)).json()
    assert [(e["row"], e["message"].split(" ")[0]) for e in b["errors"]] == [(2, "row"), (3, "type"), (5, "row")]
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/uploads/{b['id']}/scored.csv", headers=h(owner)).text)))
    assert [r["row"] for r in rows] == ["1", "4", "6"]


def test_error_list_is_capped(client, owner):
    b = post(client, owner, make(LEGIT_ROW, *[WIRE_ROW] * 60)).json()
    assert b["rows_invalid"] == 60 and len(b["errors"]) == uploads.MAX_ERRORS_SHOWN


@pytest.mark.parametrize("content", [
    "", "\n\n", HEADER + "\n", make(WIRE_ROW), b"\x00\x01\x02\xff\xfe binary \x00",
])
def test_unusable_files_are_rejected_clearly(client, owner, content):
    r = post(client, owner, content)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)


def test_size_and_row_limits(client, owner, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_BYTES", 200)
    assert post(client, owner, SAMPLE).status_code == 413
    monkeypatch.setattr(uploads, "MAX_BYTES", 10_000_000)
    monkeypatch.setattr(uploads, "MAX_ROWS", 3)
    r = post(client, owner, make(*[LEGIT_ROW] * 4))
    assert r.status_code == 422 and "limit is 3" in r.json()["detail"]
    assert post(client, owner, make(*[LEGIT_ROW] * 3)).status_code == 201


def test_spreadsheet_formulas_are_neutralised_in_the_download(client, owner):
    evil = make('2,TRANSFER,181,=HYPERLINK("http://x"),181,0,@SUM(A1),0,0,1')
    b = post(client, owner, evil).json()
    text = client.get(f"/api/uploads/{b['id']}/scored.csv", headers=h(owner)).text
    row = list(csv.DictReader(io.StringIO(text)))[0]
    assert row["nameOrig"].startswith("'=") and row["nameDest"].startswith("'@")


def test_filename_cannot_escape_the_upload_directory(client, owner, upload_dir):
    b = post(client, owner, make(LEGIT_ROW), name="../../evil.csv").json()
    assert sorted(p.name for p in upload_dir.iterdir()) == [f"{b['id']}.csv", f"{b['id']}.duckdb"]  # nothing outside the folder
    cd = client.get(f"/api/uploads/{b['id']}/scored.csv", headers=h(owner)).headers["content-disposition"]
    assert "evil_scored.csv" in cd and ".." not in cd


def test_workspaces_are_isolated(client, owner):
    b = post(client, owner, SAMPLE).json()
    other = secrets.token_hex(16)
    for method, url in [("get", f"/api/uploads/{b['id']}"), ("get", f"/api/uploads/{b['id']}/scored.csv"),
                        ("delete", f"/api/uploads/{b['id']}")]:
        assert getattr(client, method)(url, headers=h(other)).status_code == 404
    assert client.get("/api/uploads", headers=h(other)).json()["items"] == []
    assert [x["id"] for x in client.get("/api/uploads", headers=h(owner)).json()["items"]] == [b["id"]]


def test_delete_removes_the_batch_and_its_file(client, owner, upload_dir):
    b = post(client, owner, SAMPLE).json()
    assert client.delete(f"/api/uploads/{b['id']}", headers=h(owner)).status_code == 204
    assert not list(upload_dir.iterdir())
    assert client.get(f"/api/uploads/{b['id']}", headers=h(owner)).status_code == 404
    assert client.delete(f"/api/uploads/{b['id']}", headers=h(owner)).status_code == 404


def test_expired_file_gives_410_but_summary_survives(client, owner, upload_dir):
    b = post(client, owner, SAMPLE).json()
    (upload_dir / f"{b['id']}.csv").unlink()
    assert client.get(f"/api/uploads/{b['id']}/scored.csv", headers=h(owner)).status_code == 410
    again = client.get(f"/api/uploads/{b['id']}", headers=h(owner)).json()
    assert again["download_available"] is False and again["rows_valid"] == 500


def test_upload_limit_per_workspace(client, owner, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_BATCHES_PER_OWNER", 1)
    assert post(client, owner, make(LEGIT_ROW)).status_code == 201
    assert post(client, owner, make(LEGIT_ROW)).status_code == 409


def test_old_files_are_cleaned_up(client, owner, upload_dir):
    stale = upload_dir / "stale.csv"
    stale.write_text("x")
    os.utime(stale, (time.time() - 3 * 86400,) * 2)
    post(client, owner, make(LEGIT_ROW))
    assert not stale.exists()


def test_owner_header_required_and_sample_is_public(client):
    assert client.post("/api/uploads", files={"file": ("t.csv", b"x", "text/csv")}).status_code == 400
    assert client.get("/api/uploads").status_code == 400
    r = client.get("/api/uploads/sample.csv")
    assert r.status_code == 200 and r.text.startswith("step,type,amount")


def test_bad_ids_are_404(client, owner):
    for bid in ["nope", "123"]:
        assert client.get(f"/api/uploads/{bid}", headers=h(owner)).status_code == 404


def test_ttl_index_expires_batches():
    uploads._ensure_indexes()
    assert db.database()["upload_batches"].index_information()["ttl_created_at"]["expireAfterSeconds"] == 24 * 3600


def test_large_upload_scores_quickly(client, owner):
    rows = [FRAUD_ROW if i % 7 == 0 else LEGIT_ROW for i in range(20_000)]
    r = post(client, owner, make(*rows))
    assert r.status_code == 201
    b = r.json()
    assert b["rows_valid"] == 20_000 and b["processing_ms"] < 5000 and b["flagged"] > 2000
    assert b["flagged_shown"] == uploads.MAX_FLAGGED_STORED == len(b["flagged_rows"])


# ---------------------------------------------------------------------------------------------
# streaming / background job behaviour
# ---------------------------------------------------------------------------------------------
MESSY = make(
    LEGIT_ROW, FRAUD_ROW, WIRE_ROW, "3,TRANSFER,10,C1,10", "3,TRANSFER,abc,C1,10,0,C2,0,0,0",
    *[(FRAUD_ROW if i % 3 else LEGIT_ROW) for i in range(150)], "3,TRANSFER,-5,C1,10,0,C2,0,0,0",
    "3,CASH_IN,40,C7,100,60,C8,0,0,0", "5,TRANSFER,10,C1,10,0,C2,0,0,5", "9,TRANSFER,10,C1,10", LEGIT_ROW)


def summary(batch):
    keys = ("rows_total", "rows_valid", "rows_invalid", "rows_scored", "rows_not_modelled", "flagged", "metrics", "errors", "flagged_rows")
    return {k: batch[k] for k in keys}


def test_block_boundaries_do_not_change_the_result(client, owner, monkeypatch):
    """Tiny blocks force many batches, with malformed rows landing on and across the boundaries."""
    whole = post(client, owner, MESSY).json()
    scored_whole = client.get(f"/api/uploads/{whole['id']}/scored.csv", headers=h(owner)).text
    monkeypatch.setattr(uploads, "BLOCK_SIZE", 700)
    blocks = post(client, owner, MESSY).json()
    scored_blocks = client.get(f"/api/uploads/{blocks['id']}/scored.csv", headers=h(owner)).text
    assert summary(blocks) == summary(whole)
    assert scored_blocks == scored_whole
    assert whole["rows_invalid"] >= 5 and whole["flagged"] > 50


def test_progress_is_reported_and_finishes_at_one(client, owner):
    r = post(client, owner, make(*[LEGIT_ROW] * 5000), wait=False)
    assert r.status_code == 202
    first = r.json()
    assert first["status"] in ("processing", "done") and first["rows_estimated"] == 5000
    done = wait_for_job(client, owner, first["id"])
    assert done["status"] == "done" and done["progress"] == 1.0 and done["rows_processed"] == done["rows_total"] == 5000
    assert done["rows_per_second"] > 0


def test_deleting_a_running_upload_cleans_everything_up(client, owner, upload_dir):
    r = post(client, owner, make(*[FRAUD_ROW] * 150_000), wait=False)
    assert r.status_code == 202
    assert client.delete(f"/api/uploads/{r.json()['id']}", headers=h(owner)).status_code == 204
    assert wait_for(lambda: not uploads._running, 60)  # the job noticed and stopped
    assert not list(upload_dir.iterdir())
    assert client.get("/api/uploads", headers=h(owner)).json()["items"] == []


def test_a_batch_left_processing_by_a_restart_is_reported_failed(client, owner):
    from bson import ObjectId

    batches = uploads._ensure_indexes()
    oid = ObjectId()
    batches.insert_one({"_id": oid, "owner": owner, "filename": "x.csv", "created_at": uploads._now(), "size_bytes": 1,
                        "status": "processing", "progress": 0.4, "rows_processed": 10, "rows_estimated": 25, "ignored_columns": []})
    batch = client.get(f"/api/uploads/{oid}", headers=h(owner)).json()
    assert batch["status"] == "failed" and "interrupted" in batch["error"]
    assert client.get(f"/api/uploads/{oid}/scored.csv", headers=h(owner)).status_code == 409


def test_download_before_the_job_is_done_is_refused(client, owner):
    from bson import ObjectId

    oid = ObjectId()
    uploads._ensure_indexes().insert_one({"_id": oid, "owner": owner, "filename": "x.csv", "created_at": uploads._now(), "size_bytes": 1,
                                          "status": "processing", "progress": 0.1, "rows_processed": 1, "rows_estimated": 9, "ignored_columns": []})
    with uploads._running_lock:
        uploads._running[str(oid)] = __import__("threading").Event()
    try:
        assert client.get(f"/api/uploads/{oid}/scored.csv", headers=h(owner)).status_code == 409
    finally:
        uploads._running.pop(str(oid), None)


def wait_for(condition, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_a_million_row_file_streams_through(client, owner, upload_dir):
    """The point of the rewrite: about a million rows with memory that does not grow with the file."""
    import resource_probe

    rows = [FRAUD_ROW if i % 50 == 0 else LEGIT_ROW for i in range(1_050_000)]
    started = time.time()
    before = resource_probe.rss_mb()
    r = post(client, owner, make(*rows))
    elapsed = time.time() - started
    assert r.status_code == 201
    b = r.json()
    assert b["rows_valid"] == 1_050_000 and b["flagged"] == 21_000 and b["flagged_shown"] == uploads.MAX_FLAGGED_STORED
    assert resource_probe.rss_mb() - before < 700  # the file is ~90 MB of text; holding it as Python objects would be several GB
    assert elapsed < 120
    scored = upload_dir / f"{b['id']}.csv"
    assert scored.exists() and sum(1 for _ in open(scored, "rb")) == 1_050_001
