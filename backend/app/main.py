from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from config import ROOT

from . import cases, datasets, db, forest, search, store, uploads


@asynccontextmanager
async def lifespan(_app):
    search.start_fetch()
    try:
        search.warm_up()
    except Exception as err:  # keep serving everything else; /api/health reports the index state
        print(f"search index not usable: {err}")
    yield


app = FastAPI(title="Bank Transaction Analytics API", lifespan=lifespan)
app.include_router(search.router)
app.include_router(cases.router)
app.include_router(uploads.router)
app.include_router(datasets.router)

RESULT_SETS = {
    "overview", "data_quality", "type_summary", "hourly", "daily", "amount_bands",
    "balance_patterns", "repeat_fraud_destinations", "model_metrics", "model_info",
    "feature_importance", "threshold_curve", "search_benchmark",
}


@app.exception_handler(PyMongoError)
async def mongo_unavailable(_request, exc):
    return JSONResponse(status_code=503, content={
        "detail": "The database is not reachable. Start MongoDB (start.ps1 does it on Windows) or check MONGO_URI."})


@app.get("/api/health")
def health():
    return {"status": "ok", "search_index": search.status(), "search_index_state": search.index_state(),
            "database": db.available()}


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
    return forest.score_one(tx.model_dump())


class RevalidatedStaticFiles(StaticFiles):
    """Browsers must check with the server before reusing a page or script (a cheap 304 when unchanged),
    otherwise a visitor can keep running an old script after the app was updated."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", RevalidatedStaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
