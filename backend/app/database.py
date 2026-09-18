"""
Data access layer.

Tries to connect to a real MongoDB instance (the course-required NoSQL store).
If Mongo isn't reachable (e.g. still installing, or running on a machine
without it), transparently falls back to reading the same data straight from
the pipeline's local CSV/JSON output so the API and dashboard keep working.
Every route in this project talks to `get_store()` only - it never touches
pymongo or pandas directly, so swapping backends doesn't touch route code.
"""
import json
import os
from functools import lru_cache

import pandas as pd

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "bank_fraud_analytics"
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "processed")


class MongoStore:
    engine = "mongodb"

    def __init__(self, client, db):
        self.client = client
        self.db = db

    def summary(self):
        return self.db.summary.find_one({"_id": "summary"}, {"_id": 0})

    def model_metrics(self):
        return self.db.model_metrics.find_one({"_id": "metrics"}, {"_id": 0})

    def transactions(self, skip=0, limit=50, anomaly_only=False, account_id=None):
        query = {}
        if anomaly_only:
            query["is_anomaly"] = 1
        if account_id:
            query["account_id"] = account_id
        cursor = (
            self.db.transactions.find(query, {"_id": 0})
            .sort("anomaly_score", -1)
            .skip(skip)
            .limit(limit)
        )
        total = self.db.transactions.count_documents(query)
        return list(cursor), total

    def top_anomalies(self, limit=25):
        cursor = self.db.transactions.find({"is_anomaly": 1}, {"_id": 0}).sort("anomaly_score", -1).limit(limit)
        return list(cursor)

    def transaction_by_id(self, tx_id):
        return self.db.transactions.find_one({"transaction_id": tx_id}, {"_id": 0})


class FileStore:
    """Fallback store: reads the pipeline's CSV/JSON artifacts directly."""
    engine = "file_fallback"

    def __init__(self):
        self._df = None
        self._summary = None
        self._metrics = None

    def _load(self):
        if self._df is None:
            scored_path = os.path.join(PROCESSED_DIR, "scored.csv")
            self._df = pd.read_csv(scored_path, parse_dates=["timestamp"])
            self._df = self._df.sort_values("anomaly_score", ascending=False)
        return self._df

    def summary(self):
        if self._summary is None:
            with open(os.path.join(PROCESSED_DIR, "summary.json")) as f:
                self._summary = json.load(f)
        return self._summary

    def model_metrics(self):
        if self._metrics is None:
            with open(os.path.join(PROCESSED_DIR, "model_metrics.json")) as f:
                self._metrics = json.load(f)
        return self._metrics

    def _to_records(self, df):
        recs = json.loads(df.to_json(orient="records", date_format="iso"))
        return recs

    def transactions(self, skip=0, limit=50, anomaly_only=False, account_id=None):
        df = self._load()
        if anomaly_only:
            df = df[df["is_anomaly"] == 1]
        if account_id:
            df = df[df["account_id"] == account_id]
        total = len(df)
        page = df.iloc[skip: skip + limit]
        return self._to_records(page), total

    def top_anomalies(self, limit=25):
        df = self._load()
        page = df[df["is_anomaly"] == 1].head(limit)
        return self._to_records(page)

    def transaction_by_id(self, tx_id):
        df = self._load()
        match = df[df["transaction_id"] == tx_id]
        if match.empty:
            return None
        return self._to_records(match)[0]


@lru_cache
def get_store():
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
        db = client[DB_NAME]
        if db.transactions.estimated_document_count() == 0:
            raise RuntimeError("mongo reachable but empty - use file store until loaded")
        return MongoStore(client, db)
    except Exception:
        return FileStore()
