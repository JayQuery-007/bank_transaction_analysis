"""Fraud case management: full Create / Read / Update / Delete on MongoDB.

An analyst opens a case from a transaction, moves it through a review workflow, adds notes and
can delete it. Cases belong to an anonymous workspace (see db.current_owner), so visitors of the
public site never see or change each other's cases.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import db, search

router = APIRouter(prefix="/api/cases", tags=["cases"])

Status = Literal["new", "investigating", "confirmed_fraud", "false_positive", "closed"]
Priority = Literal["low", "medium", "high"]
STATUSES = ["new", "investigating", "confirmed_fraud", "false_positive", "closed"]
OPEN_STATUSES = {"new", "investigating"}
TRANSITIONS = {  # status -> statuses it may move to
    "new": {"investigating", "confirmed_fraud", "false_positive", "closed"},
    "investigating": {"confirmed_fraud", "false_positive", "closed"},
    "confirmed_fraud": {"investigating", "closed"},
    "false_positive": {"investigating", "closed"},
    "closed": {"investigating"},
}
MAX_CASES_PER_OWNER = 50
MAX_NOTES_PER_CASE = 100
SNAPSHOT_FIELDS = ("type", "amount", "name_orig", "name_dest", "step", "day", "hour_of_day",
                   "old_balance_orig", "new_balance_orig", "old_balance_dest", "new_balance_dest",
                   "is_fraud", "fraud_probability", "model_flag")


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tx_id: int = Field(ge=1, description="Transaction id from the search index")
    priority: Optional[Priority] = None
    assignee: str = Field("", max_length=60)
    note: str = Field("", max_length=2000)


class CasePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[Status] = None
    priority: Optional[Priority] = None
    assignee: Optional[str] = Field(None, max_length=60)
    add_note: Optional[str] = Field(None, min_length=1, max_length=2000)


def _now():
    return datetime.now(timezone.utc)


def _object_id(case_id):
    try:
        return ObjectId(case_id)
    except (InvalidId, TypeError):
        raise HTTPException(404, "Case not found")


def _public(doc):
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("owner", None)
    doc.pop("is_open", None)
    doc.pop("tx_ref", None)
    for key in ("created_at", "updated_at"):
        doc[key] = doc[key].isoformat()
    for item in doc.get("notes", []) + doc.get("history", []):
        item["at"] = item["at"].isoformat()
    doc["next_statuses"] = sorted(TRANSITIONS[doc["status"]], key=STATUSES.index)
    return doc


def _default_priority(tx):
    p = tx.get("fraud_probability")
    return "high" if p is not None and p >= 0.9 else "medium" if p is not None and p >= 0.5 else "low"


@router.post("", status_code=201)
def create_case(body: CaseCreate, owner: str = Depends(db.current_owner)):
    cases = db.collection("cases")
    if cases.count_documents({"owner": owner}) >= MAX_CASES_PER_OWNER:
        raise HTTPException(409, f"Limit of {MAX_CASES_PER_OWNER} cases reached. Delete some to open new ones.")
    tx = search.fetch_transaction(body.tx_id)
    if tx is None:
        raise HTTPException(404, f"Transaction {body.tx_id} not found")

    now = _now()
    doc = {
        "owner": owner, "tx_ref": f"dataset:{body.tx_id}", "tx_id": body.tx_id,
        "tx": {k: tx[k] for k in SNAPSHOT_FIELDS},
        "status": "new", "is_open": True,
        "priority": body.priority or _default_priority(tx),
        "assignee": body.assignee.strip(),
        "notes": [{"id": uuid.uuid4().hex[:8], "text": body.note.strip(), "at": now}] if body.note.strip() else [],
        "history": [{"at": now, "field": "status", "from": None, "to": "new"}],
        "created_at": now, "updated_at": now,
    }
    try:
        inserted = cases.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, f"A case is already open for transaction {body.tx_id}")
    doc["_id"] = inserted.inserted_id
    return _public(doc)


@router.get("")
def list_cases(
    status: Optional[Status] = None,
    q: Optional[str] = Query(None, max_length=60, description="transaction id, account id or assignee"),
    sort: Literal["updated_at", "created_at", "priority"] = "updated_at",
    skip: int = Query(0, ge=0, le=10000),
    limit: int = Query(25, ge=1, le=100),
    owner: str = Depends(db.current_owner),
):
    cases = db.collection("cases")
    query = {"owner": owner}
    if status:
        query["status"] = status
    if q and q.strip():
        term = q.strip()
        pattern = {"$regex": re.escape(term), "$options": "i"}
        query["$or"] = [{"tx.name_orig": pattern}, {"tx.name_dest": pattern}, {"assignee": pattern},
                        {"notes.text": pattern}] + ([{"tx_id": int(term)}] if term.isdigit() else [])
    cursor = cases.find(query)
    if sort == "priority":  # high first
        items = sorted(cursor, key=lambda c: ("high", "medium", "low").index(c["priority"]))
        items = items[skip: skip + limit]
    else:
        items = list(cursor.sort(sort, -1).skip(skip).limit(limit))
    return {"items": [_public(c) for c in items], "total": cases.count_documents(query), "skip": skip, "limit": limit}


@router.get("/stats")
def case_stats(owner: str = Depends(db.current_owner)):
    counts = {s: 0 for s in STATUSES}
    for row in db.collection("cases").aggregate([{"$match": {"owner": owner}},
                                                 {"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        counts[row["_id"]] = row["n"]
    return {"by_status": counts, "total": sum(counts.values()), "open": counts["new"] + counts["investigating"],
            "limit": MAX_CASES_PER_OWNER}


@router.get("/{case_id}")
def get_case(case_id: str, owner: str = Depends(db.current_owner)):
    doc = db.collection("cases").find_one({"_id": _object_id(case_id), "owner": owner})
    if doc is None:
        raise HTTPException(404, "Case not found")
    return _public(doc)


@router.patch("/{case_id}")
def update_case(case_id: str, body: CasePatch, owner: str = Depends(db.current_owner)):
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to update: send status, priority, assignee or add_note")
    cases = db.collection("cases")
    oid = _object_id(case_id)
    now = _now()
    flt = {"_id": oid, "owner": owner}

    # One pipeline-style update does everything atomically and can read the old status itself.
    # Text from the user is wrapped in $literal so a value starting with "$" is never an operator.
    stage = {"updated_at": now}
    if "status" in changes:
        target = changes["status"]
        # the allowed transition is part of the filter, so two concurrent updates can't both succeed
        flt["status"] = {"$in": [s for s, allowed in TRANSITIONS.items() if target in allowed]}
        stage["status"] = target
        stage["is_open"] = target in OPEN_STATUSES
        stage["history"] = {"$concatArrays": ["$history", [
            {"at": now, "field": "status", "from": "$status", "to": target}]]}
    if "priority" in changes:
        stage["priority"] = changes["priority"]
    if "assignee" in changes:
        stage["assignee"] = {"$literal": changes["assignee"].strip()}
    if "add_note" in changes:
        flt[f"notes.{MAX_NOTES_PER_CASE - 1}"] = {"$exists": False}
        stage["notes"] = {"$concatArrays": ["$notes", [
            {"id": uuid.uuid4().hex[:8], "text": {"$literal": changes["add_note"].strip()}, "at": now}]]}

    try:
        doc = cases.find_one_and_update(flt, [{"$set": stage}], return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        raise HTTPException(409, "Another open case already exists for this transaction")

    if doc is None:
        current = cases.find_one({"_id": oid, "owner": owner})
        if current is None:
            raise HTTPException(404, "Case not found")
        if "status" in changes and changes["status"] not in TRANSITIONS[current["status"]]:
            raise HTTPException(409, f"A {current['status']} case cannot move to {changes['status']}")
        raise HTTPException(409, f"A case can hold at most {MAX_NOTES_PER_CASE} notes")
    return _public(doc)


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: str, owner: str = Depends(db.current_owner)):
    result = db.collection("cases").delete_one({"_id": _object_id(case_id), "owner": owner})
    if result.deleted_count == 0:
        raise HTTPException(404, "Case not found")
    return Response(status_code=204)
