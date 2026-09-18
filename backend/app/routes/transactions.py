from typing import Optional

from fastapi import APIRouter, Query

from ..database import get_store

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.get("")
def list_transactions(
    skip: int = 0,
    limit: int = Query(50, le=500),
    anomaly_only: bool = False,
    account_id: Optional[str] = None,
):
    store = get_store()
    items, total = store.transactions(skip=skip, limit=limit, anomaly_only=anomaly_only, account_id=account_id)
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{transaction_id}")
def get_transaction(transaction_id: str):
    store = get_store()
    tx = store.transaction_by_id(transaction_id)
    if tx is None:
        return {"error": "not found"}
    return tx
