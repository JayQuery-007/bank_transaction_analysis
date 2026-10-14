"""The hosted container has no data lake: it downloads the prebuilt search index on startup."""
import os
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import search
from backend.app.main import app
from serving import fetch_index


@pytest.fixture
def isolated_search(tmp_path, monkeypatch):
    """Point the search module at an empty location and restore everything afterwards."""
    target = tmp_path / "search.duckdb"
    monkeypatch.setattr(search, "SEARCH_DB", target)
    monkeypatch.setattr(fetch_index, "SEARCH_DB", target)
    monkeypatch.setattr(search, "_con", None)
    monkeypatch.setattr(search, "_fetch", {"state": "idle", "error": None})
    monkeypatch.setenv("SEARCH_INDEX_REPO", "someone/paysim-search-index")
    return target


def make_tiny_index(path):
    """Built in a separate process, like a real download: the file never has an in-process writer."""
    sql = ("CREATE TABLE transactions AS SELECT 1 AS tx_id, 1 AS step, 1 AS day, 1 AS hour_of_day, "
           "'TRANSFER' AS type, 10.0 AS amount, 'C1' AS name_orig, 10.0 AS old_balance_orig, "
           "0.0 AS new_balance_orig, 'C2' AS name_dest, 0.0 AS old_balance_dest, "
           "0.0 AS new_balance_dest, 0 AS is_fraud, 0 AS is_flagged_fraud")
    part = path.with_name(path.name + ".incomplete")  # like hf_hub_download: written aside, then renamed
    subprocess.run([sys.executable, "-c", f"import duckdb; c = duckdb.connect(r'{part}'); c.execute({sql!r}); c.close()"],
                   check=True)
    os.replace(part, path)


def wait_for(condition, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_missing_index_without_a_source_reports_missing(isolated_search, monkeypatch):
    monkeypatch.delenv("SEARCH_INDEX_REPO")
    search.start_fetch()
    assert search.index_state() == "missing"
    with TestClient(app) as c:
        assert c.get("/api/health").json()["search_index_state"] == "missing"
        assert c.get("/api/search").status_code == 503


def test_index_is_downloaded_in_the_background_and_then_served(isolated_search, monkeypatch):
    release = threading.Event()

    def slow_fetch(*_args, **_kwargs):
        release.wait(5)
        make_tiny_index(isolated_search)
        return True

    monkeypatch.setattr(fetch_index, "fetch", slow_fetch)
    with TestClient(app) as c:
        assert search.index_state() == "downloading"
        health = c.get("/api/health").json()
        assert health["search_index_state"] == "downloading" and health["search_index"] is None
        refused = c.get("/api/search")
        assert refused.status_code == 503 and "still downloading" in refused.json()["detail"]

        release.set()
        assert wait_for(lambda: search.index_state() == "ready")
        found = c.get("/api/search", params={"account": "C1"}).json()
        assert found["total"] == 1 and found["items"][0]["tx_id"] == 1
        assert c.get("/api/health").json()["search_index"]["rows"] == 1
    if search._con is not None:
        search._con.close()


def test_a_failed_download_is_reported_not_fatal(isolated_search, monkeypatch):
    def broken_fetch(*_args, **_kwargs):
        raise OSError("repository not found")

    monkeypatch.setattr(fetch_index, "fetch", broken_fetch)
    with TestClient(app) as c:
        assert wait_for(lambda: search.index_state() == "failed")
        assert c.get("/api/health").status_code == 200  # the rest of the app keeps working
        assert c.post("/api/predict", json={
            "type": "TRANSFER", "amount": 5, "hour_of_day": 3, "old_balance_orig": 5, "new_balance_orig": 0,
            "old_balance_dest": 0, "new_balance_dest": 0}).status_code == 200
    assert "repository not found" in search._fetch["error"]


def test_fetch_asks_the_hub_for_the_dataset_file_with_the_token(isolated_search, monkeypatch):
    calls = {}

    def fake_download(**kwargs):
        calls.update(kwargs)
        make_tiny_index(isolated_search)
        return str(isolated_search)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    assert fetch_index.fetch(token="hf_read_only") is True
    assert calls["repo_id"] == "someone/paysim-search-index" and calls["repo_type"] == "dataset"
    assert calls["filename"] == "search.duckdb" and calls["token"] == "hf_read_only"


def test_fetch_does_nothing_when_the_index_already_exists(isolated_search, monkeypatch):
    make_tiny_index(isolated_search)
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda **_k: pytest.fail("should not download"))
    assert fetch_index.fetch() is True


def test_fetch_without_a_repo_returns_false(isolated_search, monkeypatch):
    monkeypatch.delenv("SEARCH_INDEX_REPO")
    assert fetch_index.fetch() is False
