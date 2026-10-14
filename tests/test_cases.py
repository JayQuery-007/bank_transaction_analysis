import secrets

import pytest
from fastapi.testclient import TestClient

from backend.app import db, search
from backend.app.main import app
from config import SEARCH_DB

pytestmark = [
    pytest.mark.skipif(not SEARCH_DB.exists(), reason="needs the search index (python run_pipeline.py)"),
    pytest.mark.skipif(not db.available(), reason="needs MongoDB on localhost:27017 (or MONGO_URI)"),
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def owner():
    return secrets.token_hex(16)  # a fresh, empty workspace per test


def headers(owner):
    return {"X-Owner": owner}


def fraud_tx_ids(n):
    cur = search._connection().cursor()
    return [r[0] for r in cur.execute(
        "SELECT tx_id FROM transactions WHERE is_fraud = 1 AND type = 'TRANSFER' ORDER BY tx_id LIMIT ?", [n]).fetchall()]


def test_create_read_update_delete_lifecycle(client, owner):
    tx_id = fraud_tx_ids(1)[0]
    created = client.post("/api/cases", json={"tx_id": tx_id, "assignee": " Priya ", "note": "Sender emptied"},
                          headers=headers(owner))
    assert created.status_code == 201
    case = created.json()
    assert case["status"] == "new" and case["tx_id"] == tx_id and case["assignee"] == "Priya"
    assert case["priority"] == "high" and case["tx"]["fraud_probability"] > 0.9
    assert case["notes"][0]["text"] == "Sender emptied" and "owner" not in case
    assert case["next_statuses"] == ["investigating", "confirmed_fraud", "false_positive", "closed"]

    cid = case["id"]
    assert client.get(f"/api/cases/{cid}", headers=headers(owner)).json()["id"] == cid

    updated = client.patch(f"/api/cases/{cid}", headers=headers(owner),
                           json={"status": "investigating", "priority": "medium", "add_note": "Called the customer"}).json()
    assert updated["status"] == "investigating" and updated["priority"] == "medium"
    assert [n["text"] for n in updated["notes"]] == ["Sender emptied", "Called the customer"]
    assert updated["history"][-1]["from"] == "new" and updated["history"][-1]["to"] == "investigating"

    listed = client.get("/api/cases", headers=headers(owner)).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == cid

    assert client.delete(f"/api/cases/{cid}", headers=headers(owner)).status_code == 204
    assert client.get(f"/api/cases/{cid}", headers=headers(owner)).status_code == 404
    assert client.delete(f"/api/cases/{cid}", headers=headers(owner)).status_code == 404
    assert client.get("/api/cases", headers=headers(owner)).json()["total"] == 0


def test_workspaces_are_isolated(client, owner):
    other = secrets.token_hex(16)
    cid = client.post("/api/cases", json={"tx_id": fraud_tx_ids(1)[0]}, headers=headers(owner)).json()["id"]
    assert client.get(f"/api/cases/{cid}", headers=headers(other)).status_code == 404
    assert client.patch(f"/api/cases/{cid}", json={"status": "closed"}, headers=headers(other)).status_code == 404
    assert client.delete(f"/api/cases/{cid}", headers=headers(other)).status_code == 404
    assert client.get("/api/cases", headers=headers(other)).json()["total"] == 0
    assert client.get(f"/api/cases/{cid}", headers=headers(owner)).status_code == 200  # untouched


def test_owner_header_is_required_and_validated(client):
    assert client.get("/api/cases").status_code == 400
    assert client.get("/api/cases", headers={"X-Owner": "not-hex"}).status_code == 400
    assert client.get("/api/cases", headers={"X-Owner": "A" * 32}).status_code == 400  # must be lowercase hex


def test_only_one_open_case_per_transaction(client, owner):
    tx_id = fraud_tx_ids(1)[0]
    first = client.post("/api/cases", json={"tx_id": tx_id}, headers=headers(owner)).json()
    assert client.post("/api/cases", json={"tx_id": tx_id}, headers=headers(owner)).status_code == 409
    # once resolved, a new case may be opened for the same transaction
    client.patch(f"/api/cases/{first['id']}", json={"status": "false_positive"}, headers=headers(owner))
    second = client.post("/api/cases", json={"tx_id": tx_id}, headers=headers(owner))
    assert second.status_code == 201
    # ...but then the first one cannot be re-opened (that would be two open cases)
    assert client.patch(f"/api/cases/{first['id']}", json={"status": "investigating"}, headers=headers(owner)).status_code == 409
    # a different workspace can open its own case for the same transaction
    assert client.post("/api/cases", json={"tx_id": tx_id}, headers=headers(secrets.token_hex(16))).status_code == 201


def test_status_workflow_is_enforced(client, owner):
    cid = client.post("/api/cases", json={"tx_id": fraud_tx_ids(1)[0]}, headers=headers(owner)).json()["id"]
    patch = lambda status: client.patch(f"/api/cases/{cid}", json={"status": status}, headers=headers(owner))
    assert patch("closed").status_code == 200
    assert patch("confirmed_fraud").status_code == 409  # closed -> confirmed is not allowed
    assert patch("closed").status_code == 409  # same status
    assert patch("investigating").status_code == 200  # reopening is allowed
    assert patch("confirmed_fraud").status_code == 200
    assert client.get(f"/api/cases/{cid}", headers=headers(owner)).json()["status"] == "confirmed_fraud"


@pytest.mark.parametrize("body", [
    {}, {"tx_id": 0}, {"tx_id": "abc"}, {"tx_id": 5, "extra": 1}, {"tx_id": 5, "priority": "urgent"},
    {"tx_id": 5, "note": "x" * 2001}, {"tx_id": 5, "assignee": "x" * 61},
])
def test_create_validation(client, owner, body):
    assert client.post("/api/cases", json=body, headers=headers(owner)).status_code == 422


def test_unknown_transaction_is_404(client, owner):
    assert client.post("/api/cases", json={"tx_id": 999_999_999}, headers=headers(owner)).status_code == 404


@pytest.mark.parametrize("body", [{}, {"status": "reopened"}, {"priority": "x"}, {"add_note": ""}, {"add_note": "x" * 2001}, {"owner": "x"}])
def test_update_validation(client, owner, body):
    cid = client.post("/api/cases", json={"tx_id": fraud_tx_ids(1)[0]}, headers=headers(owner)).json()["id"]
    assert client.patch(f"/api/cases/{cid}", json=body, headers=headers(owner)).status_code == 422


def test_bad_ids_are_404_not_errors(client, owner):
    for cid in ["nope", "123", "z" * 24]:
        assert client.get(f"/api/cases/{cid}", headers=headers(owner)).status_code == 404
        assert client.delete(f"/api/cases/{cid}", headers=headers(owner)).status_code == 404


def test_user_text_is_never_run_as_a_mongodb_operator(client, owner):
    cid = client.post("/api/cases", json={"tx_id": fraud_tx_ids(1)[0]}, headers=headers(owner)).json()["id"]
    hostile = "$status"  # inside a pipeline update this would copy another field if not wrapped in $literal
    r = client.patch(f"/api/cases/{cid}", json={"assignee": hostile, "add_note": "$notes"}, headers=headers(owner)).json()
    assert r["assignee"] == hostile and r["notes"][-1]["text"] == "$notes"


def test_filtering_searching_and_stats(client, owner):
    a, b, c = fraud_tx_ids(3)
    ids = [client.post("/api/cases", json={"tx_id": t, "assignee": name}, headers=headers(owner)).json()["id"]
           for t, name in [(a, "Asha"), (b, "Ben"), (c, "Chen")]]
    client.patch(f"/api/cases/{ids[1]}", json={"status": "investigating"}, headers=headers(owner))
    client.patch(f"/api/cases/{ids[2]}", json={"status": "closed"}, headers=headers(owner))

    stats = client.get("/api/cases/stats", headers=headers(owner)).json()
    assert stats["by_status"]["new"] == 1 and stats["by_status"]["investigating"] == 1 and stats["by_status"]["closed"] == 1
    assert stats["total"] == 3 and stats["open"] == 2

    by_status = client.get("/api/cases", params={"status": "investigating"}, headers=headers(owner)).json()
    assert [i["id"] for i in by_status["items"]] == [ids[1]]
    assert client.get("/api/cases", params={"q": "ben"}, headers=headers(owner)).json()["total"] == 1
    assert client.get("/api/cases", params={"q": str(a)}, headers=headers(owner)).json()["items"][0]["id"] == ids[0]
    account = client.get(f"/api/cases/{ids[2]}", headers=headers(owner)).json()["tx"]["name_orig"]
    assert client.get("/api/cases", params={"q": account.lower()}, headers=headers(owner)).json()["total"] >= 1
    assert client.get("/api/cases", params={"q": ".*"}, headers=headers(owner)).json()["total"] == 0  # regex is escaped
    assert client.get("/api/cases", params={"sort": "priority", "limit": 2}, headers=headers(owner)).json()["total"] == 3


def test_case_limit_per_workspace(client, owner, monkeypatch):
    from backend.app import cases

    monkeypatch.setattr(cases, "MAX_CASES_PER_OWNER", 2)
    a, b, c = fraud_tx_ids(3)
    assert client.post("/api/cases", json={"tx_id": a}, headers=headers(owner)).status_code == 201
    assert client.post("/api/cases", json={"tx_id": b}, headers=headers(owner)).status_code == 201
    assert client.post("/api/cases", json={"tx_id": c}, headers=headers(owner)).status_code == 409


def test_note_limit_per_case(client, owner, monkeypatch):
    from backend.app import cases

    monkeypatch.setattr(cases, "MAX_NOTES_PER_CASE", 3)
    cid = client.post("/api/cases", json={"tx_id": fraud_tx_ids(1)[0], "note": "one"}, headers=headers(owner)).json()["id"]
    statuses = [client.patch(f"/api/cases/{cid}", json={"add_note": f"n{i}"}, headers=headers(owner)).status_code for i in range(3)]
    assert statuses == [200, 200, 409]


def test_ttl_index_expires_idle_cases():
    indexes = db.database()["cases"].index_information()
    assert indexes["ttl_updated_at"]["expireAfterSeconds"] == 7 * 86400
    assert indexes["one_open_case_per_tx"]["unique"]


def test_health_reports_the_database(client):
    assert client.get("/api/health").json()["database"] is True
