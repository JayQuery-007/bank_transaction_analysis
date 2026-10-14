import json

import numpy as np
from fastapi.testclient import TestClient

from backend.app import forest
from backend.app.main import app
from config import RESULTS_DIR

FLAGGED = json.loads((RESULTS_DIR / "flagged_transactions.json").read_text())


def _matrix(rows):
    return forest.feature_matrix(
        [1 if r["type"] == "TRANSFER" else 0 for r in rows], [r["amount"] for r in rows],
        [r["old_balance_orig"] for r in rows], [r["new_balance_orig"] for r in rows],
        [r["old_balance_dest"] for r in rows], [r["new_balance_dest"] for r in rows],
        [r["hour_of_day"] for r in rows],
    )


def test_matches_probabilities_stored_by_spark():
    probs = forest.fraud_probability(_matrix(FLAGGED))
    stored = np.array([r["fraud_probability"] for r in FLAGGED])
    assert np.abs(probs - stored).max() < 1e-4  # Spark stored 4 decimals


def test_decisions_match_spark_outcomes():
    probs = forest.fraud_probability(_matrix(FLAGGED))
    flagged_by_spark = np.array([r["outcome"] != "missed fraud" for r in FLAGGED])
    assert ((probs > forest.THRESHOLD) == flagged_by_spark).all()


def test_batch_equals_single_row_scoring():
    rows = FLAGGED[:25]
    batch = forest.fraud_probability(_matrix(rows))
    singles = [forest.fraud_probability(_matrix([r]))[0] for r in rows]
    assert np.allclose(batch, singles)


def test_predict_endpoint_without_spark():
    client = TestClient(app)
    fraud = client.post("/api/predict", json={
        "type": "CASH_OUT", "amount": 2658595.89, "hour_of_day": 2, "old_balance_orig": 2658595.89,
        "new_balance_orig": 0, "old_balance_dest": 286931.14, "new_balance_dest": 2945527.03}).json()
    legit = client.post("/api/predict", json={
        "type": "CASH_OUT", "amount": 18500, "hour_of_day": 14, "old_balance_orig": 42000,
        "new_balance_orig": 23500, "old_balance_dest": 125000, "new_balance_dest": 143500}).json()
    assert fraud["is_fraud"] and fraud["fraud_probability"] > 0.99
    assert not legit["is_fraud"] and legit["fraud_probability"] < 0.01


def test_predict_rejects_bad_input():
    client = TestClient(app)
    assert client.post("/api/predict", json={"type": "PAYMENT", "amount": -5}).status_code == 422
