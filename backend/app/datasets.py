"""Which datasets the Overview, Explorer and Cases tabs can work on: PaySim, or one of your uploads."""
from fastapi import APIRouter, Depends, HTTPException

from . import db, search, uploads

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("")
def list_datasets(owner: str = Depends(db.current_owner)):
    paysim_rows = (search.status() or {}).get("rows")
    items = [{"id": "paysim", "name": "Main ledger", "kind": "builtin", "rows": paysim_rows or 6362604,
              "labelled": True, "available": search.index_state() == "ready"}]
    for doc in uploads._ensure_indexes().find({"owner": owner, "status": "done"}).sort("created_at", -1):
        if uploads._db_path(str(doc["_id"])).exists():  # the searchable copy lives 24 hours
            items.append({"id": str(doc["_id"]), "name": doc["filename"], "kind": "upload", "rows": doc["rows_valid"],
                          "scored": doc["rows_scored"], "flagged": doc["flagged"], "labelled": doc.get("metrics") is not None,
                          "created_at": doc["created_at"].isoformat(), "available": True})
    return {"items": items}


@router.get("/{dataset_id}/overview")
def upload_overview(dataset_id: str, owner: str = Depends(db.current_owner)):
    """The numbers behind an uploaded file's Overview tab (PaySim's overview comes from the Spark results)."""
    doc = uploads._ensure_indexes().find_one({"_id": uploads._object_id(dataset_id), "owner": owner})
    if doc is None or doc.get("status") != "done" or "analytics" not in doc:
        raise HTTPException(404, "Dataset not found")
    return {"id": dataset_id, "name": doc["filename"], "created_at": doc["created_at"].isoformat(),
            "rows": doc["rows_valid"], "rows_invalid": doc["rows_invalid"], "rows_scored": doc["rows_scored"],
            "flagged": doc["flagged"], "metrics": doc.get("metrics"), "analytics": doc["analytics"],
            "processing_ms": doc.get("processing_ms"), "indexing_ms": doc.get("indexing_ms"), "size_bytes": doc["size_bytes"]}
