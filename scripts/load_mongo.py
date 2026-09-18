"""
Loads the pipeline's scored transactions + aggregate stats into MongoDB.

If MongoDB isn't reachable, this is simply skipped - the FastAPI backend
falls back to reading data/processed/*.csv|json directly (see
backend/app/database.py), so the app still works end to end without Mongo.
"""
import json
import os
import sys

import pandas as pd

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "bank_fraud_analytics"
PROCESSED_DIR = "data/processed"


def main():
    try:
        from pymongo import MongoClient, ASCENDING, DESCENDING
    except ImportError:
        print("[skip] pymongo not installed", file=sys.stderr)
        return

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
    except Exception as e:
        print(f"[skip] MongoDB not reachable at {MONGO_URI} ({e}). "
              f"API will use the file-based fallback store instead.", file=sys.stderr)
        return

    db = client[DB_NAME]

    df = pd.read_csv(f"{PROCESSED_DIR}/scored.csv", parse_dates=["timestamp"])
    records = json.loads(df.to_json(orient="records", date_format="iso"))

    db.transactions.drop()
    db.transactions.insert_many(records)
    db.transactions.create_index([("anomaly_score", DESCENDING)])
    db.transactions.create_index([("is_anomaly", ASCENDING)])
    db.transactions.create_index([("account_id", ASCENDING)])
    db.transactions.create_index([("transaction_id", ASCENDING)], unique=True)

    with open(f"{PROCESSED_DIR}/summary.json") as f:
        summary = json.load(f)
    db.summary.replace_one({"_id": "summary"}, {"_id": "summary", **summary}, upsert=True)

    with open(f"{PROCESSED_DIR}/model_metrics.json") as f:
        metrics = json.load(f)
    db.model_metrics.replace_one({"_id": "metrics"}, {"_id": "metrics", **metrics}, upsert=True)

    print(f"[mongo] loaded {len(records):,} transactions into "
          f"{DB_NAME}.transactions, plus summary + model_metrics docs")


if __name__ == "__main__":
    main()
