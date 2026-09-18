from fastapi import APIRouter

from ..database import get_store

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/summary")
def summary():
    store = get_store()
    s = store.summary()
    return {**s, "storage_engine": store.engine}


@router.get("/model-metrics")
def model_metrics():
    store = get_store()
    return store.model_metrics()
