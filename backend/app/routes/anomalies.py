from fastapi import APIRouter, Query

from ..database import get_store

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


@router.get("/top")
def top_anomalies(limit: int = Query(25, le=200)):
    store = get_store()
    return {"items": store.top_anomalies(limit=limit)}
