from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import ROOT

from . import predict, store

app = FastAPI(title="Bank Transaction Analytics API")

RESULT_SETS = {
    "overview", "data_quality", "type_summary", "hourly", "daily", "amount_bands",
    "balance_patterns", "repeat_fraud_destinations", "model_metrics", "model_info",
    "feature_importance", "threshold_curve",
}


@app.get("/api/source")
def data_source():
    return {"source": store.source()}


@app.get("/api/results/{name}")
def result_set(name: str):
    if name not in RESULT_SETS:
        raise HTTPException(404, f"Unknown result set '{name}'")
    return store.get(name)


@app.get("/api/flagged")
def flagged_transactions(
    outcome: Optional[Literal["true positive", "false positive", "missed fraud"]] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    rows, total = store.flagged(outcome, skip, limit)
    return {"items": rows, "total": total}


class Transaction(BaseModel):
    type: Literal["TRANSFER", "CASH_OUT"]
    amount: float = Field(gt=0)
    old_balance_orig: float = Field(ge=0)
    new_balance_orig: float = Field(ge=0)
    old_balance_dest: float = Field(ge=0)
    new_balance_dest: float = Field(ge=0)
    hour_of_day: int = Field(ge=0, le=23)


@app.post("/api/predict")
def predict_transaction(tx: Transaction):
    try:
        return predict.score(tx.model_dump())
    except SystemExit as e:  # raised by get_spark() when Java is missing
        raise HTTPException(503, str(e))


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
