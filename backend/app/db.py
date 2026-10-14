"""MongoDB connection for the operational data the dashboard writes: cases and uploads.

The analytical results are read through store.py (with a file fallback); anything a user
creates, edits or deletes needs a real database, so these collections have no fallback.
"""
import os
import re
import threading
import time
from functools import lru_cache

from fastapi import Header, HTTPException
from pymongo import ASCENDING, DESCENDING, MongoClient

from config import MONGO_DB, MONGO_URI

TTL_DAYS = int(os.environ.get("CASE_TTL_DAYS", "7"))  # idle cases are removed; 0 keeps them forever
OWNER_RE = re.compile(r"^[0-9a-f]{32}$")

_status_cache = {"at": 0.0, "ok": False}
_status_lock = threading.Lock()


@lru_cache
def _client():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000, tz_aware=True)


def database():
    return _client()[MONGO_DB]


@lru_cache
def _indexes_ready():
    """Created once per process; raises (and is retried next call) while MongoDB is unreachable."""
    cases = database()["cases"]
    cases.create_index([("owner", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    # at most one open case per transaction per owner
    cases.create_index([("owner", ASCENDING), ("tx_ref", ASCENDING)], unique=True,
                       partialFilterExpression={"is_open": True}, name="one_open_case_per_tx")
    if TTL_DAYS > 0:
        cases.create_index("updated_at", expireAfterSeconds=TTL_DAYS * 86400, name="ttl_updated_at")
    return True


def collection(name):
    _indexes_ready()
    return database()[name]


def available():
    """Cheap reachability check, cached for 10 s so /api/health stays fast."""
    with _status_lock:
        if time.time() - _status_cache["at"] > 10:
            try:
                _client().admin.command("ping")
                _status_cache["ok"] = True
            except Exception:
                _status_cache["ok"] = False
            _status_cache["at"] = time.time()
        return _status_cache["ok"]


def current_owner(x_owner: str = Header(None, description="Anonymous workspace id (32 hex characters)")):
    """Every browser keeps a random id in localStorage and sends it with each request, so
    visitors only ever see their own cases. It identifies a workspace; it is not a login."""
    if not x_owner or not OWNER_RE.match(x_owner):
        raise HTTPException(400, "Missing or invalid X-Owner header (32 hex characters)")
    return x_owner
