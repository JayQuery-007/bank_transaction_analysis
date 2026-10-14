"""CSV upload and batch scoring.

A user uploads a file of transactions in PaySim's format; every valid row is scored with the
Random Forest, and the batch (summary, accuracy against an optional isFraud column, the riskiest
rows) is stored in MongoDB. The full scored file stays on local disk for 24 hours so it can be
downloaded without filling the database with hundreds of thousands of documents.
"""
import csv
import io
import math
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.csv as pacsv
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from config import RESULTS_DIR

from . import db, forest

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", 25 * 1024 * 1024))
MAX_ROWS = int(os.environ.get("UPLOAD_MAX_ROWS", 100_000))
MAX_BATCHES_PER_OWNER = 3
MAX_ERRORS_SHOWN = 20
MAX_FLAGGED_STORED = 500
FILE_TTL_SECONDS = 24 * 3600
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", Path(tempfile.gettempdir()) / "bda_uploads"))

TYPES = {"CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"}
MODELLED = {"TRANSFER", "CASH_OUT"}

# canonical name -> accepted header spellings, compared after lower-casing and dropping "_", " ", "-"
COLUMNS = {
    "type": ["type"], "amount": ["amount"], "name_orig": ["nameorig"],
    "old_balance_orig": ["oldbalanceorg", "oldbalanceorig"], "new_balance_orig": ["newbalanceorig"],
    "name_dest": ["namedest"], "old_balance_dest": ["oldbalancedest"], "new_balance_dest": ["newbalancedest"],
}
DISPLAY_NAMES = {  # how the columns are spelled in the dataset, for error messages
    "type": "type", "amount": "amount", "name_orig": "nameOrig", "old_balance_orig": "oldbalanceOrg (or oldbalanceOrig)",
    "new_balance_orig": "newbalanceOrig", "name_dest": "nameDest", "old_balance_dest": "oldbalanceDest",
    "new_balance_dest": "newbalanceDest",
}
OPTIONAL = {"step": ["step"], "hour_of_day": ["hourofday", "hour"], "is_fraud": ["isfraud"]}
NUMERIC = ["amount", "old_balance_orig", "new_balance_orig", "old_balance_dest", "new_balance_dest"]
OUTPUT_COLUMNS = ["row", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig", "nameDest",
                  "oldbalanceDest", "newbalanceDest", "step", "isFraud", "fraud_probability", "model_flag"]
EXPECTED_HEADER = "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud"


def _normalise(header):
    return "".join(ch for ch in str(header).lower() if ch not in "_ -")


def _now():
    return datetime.now(timezone.utc)


def _object_id(batch_id):
    try:
        return ObjectId(batch_id)
    except (InvalidId, TypeError):
        raise HTTPException(404, "Upload not found")


def _file_path(batch_id):
    return UPLOAD_DIR / f"{batch_id}.csv"


def _remove_old_files():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - FILE_TTL_SECONDS
    for f in UPLOAD_DIR.glob("*.csv"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def _ensure_indexes():
    from pymongo import DESCENDING

    batches = db.collection("upload_batches")
    batches.create_index([("owner", 1), ("created_at", DESCENDING)])
    batches.create_index("created_at", expireAfterSeconds=FILE_TTL_SECONDS, name="ttl_created_at")
    return batches


def _public(doc, with_rows=False):
    out = {k: v for k, v in doc.items() if k not in ("_id", "owner", "flagged_rows")}
    out["id"] = str(doc["_id"])
    out["created_at"] = doc["created_at"].isoformat()
    out["download_available"] = _file_path(out["id"]).exists()
    if with_rows:
        out["flagged_rows"] = doc["flagged_rows"]
    return out


# ---------------------------------------------------------------------------------------------
# parsing and validation
# ---------------------------------------------------------------------------------------------
def _read_table(data):
    data = data.lstrip(b"\xef\xbb\xbf")  # UTF-8 byte order mark written by Excel
    skipped_lines = []

    def on_bad_row(row):
        skipped_lines.append(row.number)
        return "skip"

    try:
        table = pacsv.read_csv(
            io.BytesIO(data),
            # single-threaded so pyarrow can tell us which line a malformed row was on
            read_options=pacsv.ReadOptions(use_threads=False),
            parse_options=pacsv.ParseOptions(invalid_row_handler=on_bad_row),
            convert_options=pacsv.ConvertOptions(strings_can_be_null=False, auto_dict_encode=False))
    except Exception:
        raise HTTPException(422, "Could not read this file as CSV. Use a comma-separated file with a header row.")
    return table, sorted(line - 1 for line in skipped_lines if line)  # line 1 is the header


def _map_columns(table):
    by_norm = {_normalise(n): n for n in table.column_names}
    mapping, missing = {}, []
    for canonical, spellings in {**COLUMNS, **OPTIONAL}.items():
        found = next((by_norm[s] for s in spellings if s in by_norm), None)
        if found:
            mapping[canonical] = found
        elif canonical in COLUMNS:
            missing.append(DISPLAY_NAMES[canonical])
    if "step" not in mapping and "hour_of_day" not in mapping:
        missing.append("step (or hour_of_day)")
    if missing:
        raise HTTPException(422, f"Missing required column(s): {', '.join(missing)}. Expected a header like: {EXPECTED_HEADER}")
    ignored = [n for n in table.column_names if n not in mapping.values()]
    return mapping, ignored


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("empty")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not a finite number")
    return number


def _data_row_numbers(count, skipped):
    """File row number (header excluded) of each parsed row, counting the malformed rows that were skipped."""
    numbers, n, s = [], 0, 0
    for _ in range(count):
        n += 1
        while s < len(skipped) and skipped[s] <= n:
            n += 1
            s += 1
        numbers.append(n)
    return numbers


def _validate(table, mapping, skipped):
    """Returns the valid rows plus (row number, message) for the rest."""
    cols = {name: table.column(source).to_pylist() for name, source in mapping.items()}
    numbers = _data_row_numbers(table.num_rows, skipped)
    valid, errors = [], []
    for i in range(table.num_rows):
        row_number = numbers[i]
        try:
            tx_type = str(cols["type"][i]).strip().upper()
            if tx_type not in TYPES:
                raise ValueError(f"type '{str(cols['type'][i])[:20]}' is not one of {', '.join(sorted(TYPES))}")
            rec = {"row": row_number, "type": tx_type}
            for name in NUMERIC:
                try:
                    rec[name] = _number(cols[name][i])
                except (ValueError, TypeError):
                    raise ValueError(f"{name} is empty or not a number")
            if rec["amount"] <= 0:
                raise ValueError("amount must be greater than 0")
            if min(rec[n] for n in NUMERIC[1:]) < 0:
                raise ValueError("balances must not be negative")
            for name in ("name_orig", "name_dest"):
                text = str(cols[name][i]).strip()
                if not text or len(text) > 32:
                    raise ValueError(f"{name} must be 1-32 characters")
                rec[name] = text
            if "step" in cols:
                step = _number(cols["step"][i])
                if step < 1 or step != int(step):
                    raise ValueError("step must be a whole number of at least 1")
                rec["step"] = int(step)
                rec["hour_of_day"] = int(step) % 24
            else:
                hour = _number(cols["hour_of_day"][i])
                if not (0 <= hour <= 23) or hour != int(hour):
                    raise ValueError("hour_of_day must be a whole number from 0 to 23")
                rec["step"] = None
                rec["hour_of_day"] = int(hour)
            if "is_fraud" in cols:
                label = str(cols["is_fraud"][i]).strip().lower()
                if label not in ("0", "1", "0.0", "1.0", "true", "false"):
                    raise ValueError("isFraud must be 0 or 1")
                rec["is_fraud"] = 1 if label in ("1", "1.0", "true") else 0
            else:
                rec["is_fraud"] = None
            valid.append(rec)
        except ValueError as err:
            errors.append((row_number, str(err)))
    return valid, errors


def _score(valid):
    idx = [i for i, r in enumerate(valid) if r["type"] in MODELLED]
    for r in valid:
        r["fraud_probability"] = None
        r["model_flag"] = None
    if idx:
        x = forest.feature_matrix(
            [1 if valid[i]["type"] == "TRANSFER" else 0 for i in idx], [valid[i]["amount"] for i in idx],
            [valid[i]["old_balance_orig"] for i in idx], [valid[i]["new_balance_orig"] for i in idx],
            [valid[i]["old_balance_dest"] for i in idx], [valid[i]["new_balance_dest"] for i in idx],
            [valid[i]["hour_of_day"] for i in idx])
        for i, p in zip(idx, forest.fraud_probability(x)):
            valid[i]["fraud_probability"] = round(float(p), 4)
            valid[i]["model_flag"] = bool(p > forest.THRESHOLD)
    return len(idx)


def _metrics(valid):
    """Accuracy against the file's isFraud column, on the rows the model scores."""
    rows = [r for r in valid if r["model_flag"] is not None and r["is_fraud"] is not None]
    if not rows:
        return None
    truth = np.array([r["is_fraud"] for r in rows], dtype=bool)
    flag = np.array([r["model_flag"] for r in rows], dtype=bool)
    tp, fp = int((truth & flag).sum()), int((~truth & flag).sum())
    fn, tn = int((truth & ~flag).sum()), int((~truth & ~flag).sum())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    rnd = lambda v: None if v is None else round(v, 4)
    return {"rows": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": rnd(precision), "recall": rnd(recall), "f1": rnd(f1)}


# ---------------------------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------------------------
def _safe_cell(value):
    """Stops spreadsheet formula injection: a text cell starting with = + - @ would run as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _write_scored_csv(path, valid):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        for r in valid:
            writer.writerow([
                r["row"], r["type"], r["amount"], _safe_cell(r["name_orig"]), r["old_balance_orig"],
                r["new_balance_orig"], _safe_cell(r["name_dest"]), r["old_balance_dest"], r["new_balance_dest"],
                "" if r["step"] is None else r["step"], "" if r["is_fraud"] is None else r["is_fraud"],
                "" if r["fraud_probability"] is None else r["fraud_probability"],
                "" if r["model_flag"] is None else str(r["model_flag"]).lower()])


# ---------------------------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------------------------
@router.get("/sample.csv")
def sample_file():
    path = RESULTS_DIR / "sample_upload.csv"
    if not path.exists():
        raise HTTPException(404, "Sample file not found")
    return FileResponse(path, media_type="text/csv", filename="sample_transactions.csv")


@router.post("", status_code=201)
def upload(file: UploadFile = File(...), owner: str = Depends(db.current_owner)):
    batches = _ensure_indexes()
    if batches.count_documents({"owner": owner}) >= MAX_BATCHES_PER_OWNER:
        raise HTTPException(409, f"Limit of {MAX_BATCHES_PER_OWNER} uploads reached. Delete one to upload another.")

    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if not data.strip():
        raise HTTPException(422, "The file is empty")

    table, skipped = _read_table(data)
    if table.num_rows == 0:
        raise HTTPException(422, "The file has a header but no rows")
    if table.num_rows > MAX_ROWS:
        raise HTTPException(422, f"The file has {table.num_rows:,} rows; the limit is {MAX_ROWS:,}")
    mapping, ignored = _map_columns(table)

    valid, errors = _validate(table, mapping, skipped)
    errors += [(n, "row has the wrong number of columns") for n in skipped]
    errors.sort()
    if not valid:
        raise HTTPException(422, f"No valid rows. First problem: row {errors[0][0]}: {errors[0][1]}" if errors else "No valid rows")

    started = time.perf_counter()
    scored = _score(valid)
    scoring_ms = (time.perf_counter() - started) * 1000
    flagged = sorted((r for r in valid if r["model_flag"]), key=lambda r: -r["fraud_probability"])

    doc = {
        "owner": owner, "filename": (file.filename or "upload.csv")[:120], "created_at": _now(), "size_bytes": len(data),
        "rows_total": table.num_rows + len(skipped), "rows_valid": len(valid), "rows_invalid": len(errors),
        "rows_scored": scored, "rows_not_modelled": len(valid) - scored, "flagged": len(flagged),
        "scoring_ms": round(scoring_ms, 1), "ignored_columns": [str(c)[:40] for c in ignored[:20]],
        "errors": [{"row": n, "message": m} for n, m in errors[:MAX_ERRORS_SHOWN]],
        "metrics": _metrics(valid), "flagged_shown": min(len(flagged), MAX_FLAGGED_STORED),
        "flagged_rows": flagged[:MAX_FLAGGED_STORED],
    }
    inserted = batches.insert_one(doc)
    doc["_id"] = inserted.inserted_id

    _remove_old_files()
    _write_scored_csv(_file_path(str(inserted.inserted_id)), valid)
    return _public(doc, with_rows=True)


@router.get("")
def list_uploads(owner: str = Depends(db.current_owner)):
    batches = _ensure_indexes()
    return {"items": [_public(d) for d in batches.find({"owner": owner}).sort("created_at", -1)],
            "limit": MAX_BATCHES_PER_OWNER}


@router.get("/{batch_id}")
def get_upload(batch_id: str, owner: str = Depends(db.current_owner)):
    doc = _ensure_indexes().find_one({"_id": _object_id(batch_id), "owner": owner})
    if doc is None:
        raise HTTPException(404, "Upload not found")
    return _public(doc, with_rows=True)


@router.get("/{batch_id}/scored.csv")
def download_scored(batch_id: str, owner: str = Depends(db.current_owner)):
    doc = _ensure_indexes().find_one({"_id": _object_id(batch_id), "owner": owner})
    if doc is None:
        raise HTTPException(404, "Upload not found")
    path = _file_path(batch_id)
    if not path.exists():
        raise HTTPException(410, "The scored file has expired (files are kept for 24 hours). Upload the file again.")
    name = Path(doc["filename"]).stem or "upload"
    return FileResponse(path, media_type="text/csv", filename=f"{name}_scored.csv")


@router.delete("/{batch_id}", status_code=204)
def delete_upload(batch_id: str, owner: str = Depends(db.current_owner)):
    result = _ensure_indexes().delete_one({"_id": _object_id(batch_id), "owner": owner})
    if result.deleted_count == 0:
        raise HTTPException(404, "Upload not found")
    _file_path(batch_id).unlink(missing_ok=True)
    return Response(status_code=204)
