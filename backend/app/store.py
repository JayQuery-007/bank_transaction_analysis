"""Reads analysis results from MongoDB, or from results/*.json if MongoDB isn't running.

The Spark jobs write every result set to both places, and results/ is committed to
the repo, so the dashboard works on a fresh clone without running the pipeline.
"""
import json
from functools import lru_cache

from config import MONGO_DB, MONGO_URI, RESULTS_DIR


@lru_cache
def _mongo_db():
    try:
        from pymongo import MongoClient

        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
        db = client[MONGO_DB]
        if db.overview.count_documents({}) == 0:
            return None
        return db
    except Exception:
        return None


def source():
    return "MongoDB" if _mongo_db() is not None else "results/*.json"


def get(name):
    db = _mongo_db()
    if db is not None:
        docs = list(db[name].find({}, {"_id": 0}))
        if docs:
            return docs
        # a result set added after MongoDB was last loaded is still available from results/
    path = RESULTS_DIR / f"{name}.json"
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def flagged(outcome=None, skip=0, limit=50):
    db = _mongo_db()
    query = {"outcome": outcome} if outcome else {}
    if db is not None:
        coll = db.flagged_transactions
        rows = coll.find(query, {"_id": 0}).sort("fraud_probability", -1).skip(skip).limit(limit)
        return list(rows), coll.count_documents(query)
    rows = get("flagged_transactions")
    if outcome:
        rows = [r for r in rows if r["outcome"] == outcome]
    return rows[skip: skip + limit], len(rows)
