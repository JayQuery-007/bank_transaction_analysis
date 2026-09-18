import os
from datetime import datetime
from functools import lru_cache

import joblib
import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/detect", tags=["detect"])

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ml", "model.joblib")


class TransactionInput(BaseModel):
    amount: float = Field(..., gt=0)
    hour: int = Field(..., ge=0, le=23)
    account_avg_amount: float = Field(..., gt=0)
    account_std_amount: float = Field(0.0, ge=0)
    night_ratio: float = Field(0.05, ge=0, le=1)
    distinct_countries: int = Field(1, ge=1)
    distinct_categories: int = Field(1, ge=1)
    seconds_since_prev_tx: float = Field(3600.0, ge=0)


@lru_cache
def _load_model():
    bundle = joblib.load(MODEL_PATH)
    return bundle["model"], bundle["scaler"], bundle["feature_cols"]


@router.post("")
def detect(tx: TransactionInput):
    model, scaler, feature_cols = _load_model()

    is_night = 1 if tx.hour <= 5 else 0
    amount_zscore = (
        (tx.amount - tx.account_avg_amount) / tx.account_std_amount
        if tx.account_std_amount > 0 else 0.0
    )
    velocity_flag = 1 if tx.seconds_since_prev_tx < 60 else 0

    feature_values = {
        "amount": tx.amount,
        "amount_zscore": amount_zscore,
        "is_night": is_night,
        "night_ratio": tx.night_ratio,
        "distinct_countries": tx.distinct_countries,
        "distinct_categories": tx.distinct_categories,
        "hour": tx.hour,
        "velocity_flag": velocity_flag,
    }
    X = np.array([[feature_values[c] for c in feature_cols]])
    X_scaled = scaler.transform(X)

    raw_score = -model.decision_function(X_scaled)[0]
    pred = model.predict(X_scaled)[0]

    return {
        "is_anomaly": bool(pred == -1),
        "raw_anomaly_score": round(float(raw_score), 4),
        "amount_zscore": round(amount_zscore, 3),
        "checked_at": datetime.utcnow().isoformat(),
        "features_used": feature_values,
    }
