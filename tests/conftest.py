import os
import sys
from pathlib import Path

# Tests write to MongoDB, so they always use their own database, never the real one.
os.environ["MONGO_DB"] = "bank_analytics_test"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def drop_test_database():
    yield
    try:
        from pymongo import MongoClient

        from config import MONGO_URI

        MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000).drop_database("bank_analytics_test")
    except Exception:
        pass
