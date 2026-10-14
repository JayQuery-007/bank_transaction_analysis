"""CSV upload and batch scoring, streamed so files of millions of rows use little memory.

The uploaded file is saved to disk, then a background job reads it in blocks (pyarrow's streaming
CSV reader), validates and scores each block with vectorised numpy/Arrow code, and appends the
scored rows to an output CSV. Only counters and the 500 riskiest rows are kept in memory. The
batch (status, progress, summary, accuracy against an optional isFraud column, riskiest rows)
is stored in MongoDB; the full scored CSV stays on local disk for 24 hours.
"""
import csv
import os
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from config import RESULTS_DIR

from . import db, forest

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# Generous defaults for running locally; the Dockerfile sets small ones for the public deployment.
MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES", 1024 * 1024 * 1024))
MAX_ROWS = int(os.environ.get("UPLOAD_MAX_ROWS", 12_000_000))
BLOCK_SIZE = 16 * 1024 * 1024  # bytes of CSV parsed at a time (~190,000 rows)
MAX_BATCHES_PER_OWNER = 3
MAX_ERRORS_SHOWN = 20
MAX_FLAGGED_STORED = 500
FILE_TTL_SECONDS = 24 * 3600
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", Path(tempfile.gettempdir()) / "bda_uploads"))
BOM = b"\xef\xbb\xbf"

TYPES = ["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"]
MODELLED = ["TRANSFER", "CASH_OUT"]
NUMBER_RE = r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$"

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
EXPECTED_HEADER = "step,type,amount,nameOrig,oldbalanceOrg,newbalanceOrig,nameDest,oldbalanceDest,newbalanceDest,isFraud"

_jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="upload")  # at most two files processed at once
_running = {}  # batch id -> cancel event, for jobs this process is working on
_running_lock = threading.Lock()


class UploadError(Exception):
    """A problem with the file's content, reported to the user as the batch's error."""


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


def _db_path(batch_id):
    return UPLOAD_DIR / f"{batch_id}.duckdb"


def _remove_old_files():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - FILE_TTL_SECONDS
    for pattern in ("*.csv", "*.part", "*.upload", "*.duckdb", "*.building", "*.wal"):
        for f in UPLOAD_DIR.glob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    _close_dataset_connection(f.stem)
                    f.unlink(missing_ok=True)
            except OSError:  # still open on Windows; it is removed on a later upload
                pass


# Read-only connections to the per-upload search databases; a few are kept open, the oldest is closed first.
_dataset_connections = OrderedDict()
_dataset_lock = threading.Lock()
MAX_OPEN_DATASETS = 3


def _close_dataset_connection(batch_id):
    with _dataset_lock:
        con = _dataset_connections.pop(batch_id, None)
    if con is not None:
        con.close()


def dataset_connection(batch_id, owner):
    """(connection, batch document) for one of the owner's finished uploads. Raises 404 / 410."""
    doc = _ensure_indexes().find_one({"_id": _object_id(batch_id), "owner": owner})
    if doc is None or doc.get("status") != "done":
        raise HTTPException(404, "Dataset not found")
    path = _db_path(batch_id)
    if not path.exists():
        raise HTTPException(410, "This upload's search index has expired (kept for 24 hours). Upload the file again.")
    with _dataset_lock:
        con = _dataset_connections.get(batch_id)
        if con is None:
            con = duckdb.connect(str(path), read_only=True)
            con.execute("PRAGMA memory_limit='512MB'")
            _dataset_connections[batch_id] = con
            while len(_dataset_connections) > MAX_OPEN_DATASETS:
                _dataset_connections.popitem(last=False)[1].close()
        else:
            _dataset_connections.move_to_end(batch_id)
    return con, doc


def _ensure_indexes():
    from pymongo import DESCENDING

    batches = db.collection("upload_batches")
    batches.create_index([("owner", 1), ("created_at", DESCENDING)])
    batches.create_index("created_at", expireAfterSeconds=FILE_TTL_SECONDS, name="ttl_created_at")
    return batches


def _public(doc, with_rows=False):
    out = {k: v for k, v in doc.items() if k not in ("_id", "owner", "flagged_rows", "analytics")}
    out["id"] = str(doc["_id"])
    out["created_at"] = doc["created_at"].isoformat()
    if out["status"] == "processing" and out["id"] not in _running:
        out["status"], out["error"] = "failed", "Processing was interrupted (the server restarted). Upload the file again."
    out["download_available"] = out["status"] == "done" and _file_path(out["id"]).exists()
    if with_rows and "flagged_rows" in doc:
        out["flagged_rows"] = doc["flagged_rows"]
    return out


# ---------------------------------------------------------------------------------------------
# accepting the file
# ---------------------------------------------------------------------------------------------
def _save_limited(source, dest):
    size = 0
    with open(dest, "wb") as out:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File is larger than {max(MAX_BYTES // (1024 * 1024), 1)} MB")
            out.write(chunk)
    return size


def _inspect(path):
    """Checks the header and estimates the row count without parsing the file. Raises HTTPException."""
    with open(path, "rb") as f:
        head = f.read(65536)
        if not head.strip():
            raise HTTPException(422, "The file is empty")
        if head.startswith(BOM):
            head = head[len(BOM):]
        try:
            first_line = head.split(b"\n", 1)[0].decode("utf-8").strip("\r")
            names = next(csv.reader([first_line]))
        except (UnicodeDecodeError, StopIteration, csv.Error):
            raise HTTPException(422, "Could not read this file as CSV. Use a comma-separated UTF-8 file with a header row.")
        lines, last = 0, b"\n"
        f.seek(0)
        while chunk := f.read(8 * 1024 * 1024):
            lines += chunk.count(b"\n")
            last = chunk[-1:]
        if last != b"\n":
            lines += 1
    rows = lines - 1
    if rows < 1:
        raise HTTPException(422, "The file has a header but no rows")
    if rows > MAX_ROWS:
        raise HTTPException(422, f"The file has {rows:,} rows; the limit is {MAX_ROWS:,}")
    return names, _map_columns(names), rows


def _map_columns(names):
    by_norm = {_normalise(n): n for n in names}
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
    return mapping


# ---------------------------------------------------------------------------------------------
# validating and scoring one block of rows (vectorised)
# ---------------------------------------------------------------------------------------------
def _numbers(arr):
    """Text column -> (float64 values, mask of cells that really are finite numbers)."""
    trimmed = pc.utf8_trim_whitespace(arr)
    looks_numeric = pc.match_substring_regex(trimmed, NUMBER_RE)
    cleaned = pc.if_else(looks_numeric, trimmed, pa.scalar(None, pa.string()))
    try:
        values = pc.cast(cleaned, pa.float64()).fill_null(float("nan")).to_numpy(zero_copy_only=False)
    except pa.ArrowInvalid:  # a number Arrow cannot parse: fall back to Python for this block
        values = np.array([float(v) if v is not None else np.nan for v in cleaned.to_pylist()])
    ok = np.isfinite(values)
    return np.where(ok, values, 0.0), ok


def _first_failure(batch, m):
    """Validates a block. Returns (valid mask, columns, error codes) where code 0 means the row is fine."""
    n = batch.num_rows
    col = lambda name: batch.column(batch.schema.get_field_index(m[name]))
    errors = np.zeros(n, dtype=np.int16)
    messages = {}

    def fail(mask, code, message):
        nonlocal errors
        messages[code] = message
        errors = np.where((errors == 0) & mask, code, errors)

    type_s = pc.utf8_upper(pc.utf8_trim_whitespace(col("type")))
    type_ok = pc.is_in(type_s, value_set=pa.array(TYPES)).to_numpy(zero_copy_only=False)
    fail(~type_ok, 1, None)  # message includes the offending value, built per row

    nums, oks = {}, {}
    for i, name in enumerate(NUMERIC):
        nums[name], oks[name] = _numbers(col(name))
        fail(~oks[name], 10 + i, f"{name} is empty or not a number")
    fail(nums["amount"] <= 0, 20, "amount must be greater than 0")
    fail(np.min([nums[k] for k in NUMERIC[1:]], axis=0) < 0, 21, "balances must not be negative")

    names = {}
    for name in ("name_orig", "name_dest"):
        names[name] = pc.utf8_trim_whitespace(col(name))
        length = pc.utf8_length(names[name]).to_numpy(zero_copy_only=False)
        fail((length < 1) | (length > 32), 30 if name == "name_orig" else 31, f"{name} must be 1-32 characters")

    if "step" in m:
        step, step_ok = _numbers(col("step"))
        fail(~step_ok | (step < 1) | (step != np.floor(step)), 40, "step must be a whole number of at least 1")
        step = step.astype(np.int64)
        hour = step % 24
    else:
        hour_f, hour_ok = _numbers(col("hour_of_day"))
        fail(~hour_ok | (hour_f < 0) | (hour_f > 23) | (hour_f != np.floor(hour_f)), 41, "hour_of_day must be a whole number from 0 to 23")
        hour = hour_f.astype(np.int64)
        step = None

    label = None
    if "is_fraud" in m:
        text = pc.utf8_lower(pc.utf8_trim_whitespace(col("is_fraud")))
        label_ok = pc.is_in(text, value_set=pa.array(["0", "1", "0.0", "1.0", "true", "false"])).to_numpy(zero_copy_only=False)
        fail(~label_ok, 50, "isFraud must be 0 or 1")
        label = pc.is_in(text, value_set=pa.array(["1", "1.0", "true"])).to_numpy(zero_copy_only=False)

    return {"n": n, "errors": errors, "messages": messages, "type": type_s, "nums": nums, "names": names,
            "hour": hour, "step": step, "label": label, "col": col}


def _error_text(v, i):
    code = int(v["errors"][i])
    if code == 1:
        return f"type '{str(v['col']('type')[i].as_py())[:20]}' is not one of {', '.join(sorted(TYPES))}"
    return v["messages"][code]


def _process_block(batch, mapping, row_numbers, acc, writer_holder, out_part):
    v = _first_failure(batch, mapping)
    errors, n = v["errors"], v["n"]
    valid = errors == 0

    bad = np.flatnonzero(~valid)
    acc["invalid"] += len(bad)
    for i in bad[: max(0, MAX_ERRORS_SHOWN - len(acc["errors"]))]:
        acc["errors"].append((int(row_numbers[i]), _error_text(v, i)))

    type_s = v["type"]
    modelled = valid & pc.is_in(type_s, value_set=pa.array(MODELLED)).to_numpy(zero_copy_only=False)
    prob = np.full(n, np.nan)
    if modelled.any():
        m = np.flatnonzero(modelled)
        nums = v["nums"]
        is_transfer = pc.equal(type_s, "TRANSFER").to_numpy(zero_copy_only=False)
        x = forest.feature_matrix(is_transfer[m], nums["amount"][m], nums["old_balance_orig"][m], nums["new_balance_orig"][m],
                                  nums["old_balance_dest"][m], nums["new_balance_dest"][m], v["hour"][m])
        prob[m] = np.round(forest.fraud_probability(x), 4)
    flag = modelled & (prob > forest.THRESHOLD)

    acc["valid"] += int(valid.sum())
    acc["scored"] += int(modelled.sum())
    acc["flagged"] += int(flag.sum())
    if v["label"] is not None:
        truth = v["label"]
        acc["tp"] += int((modelled & truth & flag).sum())
        acc["fp"] += int((modelled & ~truth & flag).sum())
        acc["fn"] += int((modelled & truth & ~flag).sum())
        acc["tn"] += int((modelled & ~truth & ~flag).sum())

    # keep only the riskiest flagged rows of this block, merged with what we already hold
    candidates = np.flatnonzero(flag)
    if len(candidates):
        top = candidates[np.argsort(-prob[candidates], kind="stable")[:MAX_FLAGGED_STORED]]
        nums = v["nums"]
        names_o = v["names"]["name_orig"].take(pa.array(top)).to_pylist()
        names_d = v["names"]["name_dest"].take(pa.array(top)).to_pylist()
        types = type_s.take(pa.array(top)).to_pylist()
        for k, i in enumerate(top):
            acc["top"].append({
                "row": int(row_numbers[i]), "type": types[k], "amount": float(nums["amount"][i]), "name_orig": names_o[k],
                "old_balance_orig": float(nums["old_balance_orig"][i]), "new_balance_orig": float(nums["new_balance_orig"][i]),
                "name_dest": names_d[k], "old_balance_dest": float(nums["old_balance_dest"][i]),
                "new_balance_dest": float(nums["new_balance_dest"][i]), "step": None if v["step"] is None else int(v["step"][i]),
                "hour_of_day": int(v["hour"][i]), "is_fraud": None if v["label"] is None else int(v["label"][i]),
                "fraud_probability": float(prob[i]), "model_flag": True})
        acc["top"].sort(key=lambda r: -r["fraud_probability"])
        del acc["top"][MAX_FLAGGED_STORED:]

    keep = np.flatnonzero(valid)
    if len(keep):
        _append_output(v, keep, prob, row_numbers, writer_holder, out_part)


def _spreadsheet_safe(arr):
    """A text cell starting with = + - @ would run as a formula when the CSV is opened in a spreadsheet."""
    risky = pc.match_substring_regex(arr, r"^[=+\-@\t\r]")
    return pc.if_else(risky, pc.binary_join_element_wise(pa.scalar("'"), arr, pa.scalar("")), arr)


def _append_output(v, keep, prob, row_numbers, writer_holder, out_part):
    take = pa.array(keep)
    nums = v["nums"]
    probs = prob[keep]
    has_prob = ~np.isnan(probs)
    flags = np.where(has_prob, np.where(probs > forest.THRESHOLD, "true", "false"), "")
    table = pa.table({
        "row": pa.array(row_numbers[keep]),
        "type": v["type"].take(take),
        "amount": pa.array(nums["amount"][keep]),
        "nameOrig": _spreadsheet_safe(v["names"]["name_orig"].take(take)),
        "oldbalanceOrg": pa.array(nums["old_balance_orig"][keep]),
        "newbalanceOrig": pa.array(nums["new_balance_orig"][keep]),
        "nameDest": _spreadsheet_safe(v["names"]["name_dest"].take(take)),
        "oldbalanceDest": pa.array(nums["old_balance_dest"][keep]),
        "newbalanceDest": pa.array(nums["new_balance_dest"][keep]),
        "step": pa.array(np.zeros(len(keep), dtype=np.int64) if v["step"] is None else v["step"][keep], mask=np.full(len(keep), v["step"] is None)),
        "hour_of_day": pa.array(v["hour"][keep]),
        "isFraud": pa.array(np.zeros(len(keep), dtype=np.int64) if v["label"] is None else v["label"][keep].astype(np.int64),
                            mask=np.full(len(keep), v["label"] is None)),
        "fraud_probability": pa.array(np.nan_to_num(probs), mask=~has_prob),
        "model_flag": pa.array(flags),
    })
    if writer_holder[0] is None:
        writer_holder[0] = pacsv.CSVWriter(str(out_part), table.schema)
    writer_holder[0].write_table(table)


# ---------------------------------------------------------------------------------------------
# a search index and overview numbers for each upload
# ---------------------------------------------------------------------------------------------
SCORED_COLUMNS = ("{'row': 'BIGINT', 'type': 'VARCHAR', 'amount': 'DOUBLE', 'nameOrig': 'VARCHAR', 'oldbalanceOrg': 'DOUBLE', "
                  "'newbalanceOrig': 'DOUBLE', 'nameDest': 'VARCHAR', 'oldbalanceDest': 'DOUBLE', 'newbalanceDest': 'DOUBLE', "
                  "'step': 'BIGINT', 'hour_of_day': 'BIGINT', 'isFraud': 'BIGINT', 'fraud_probability': 'DOUBLE', 'model_flag': 'VARCHAR'}")


def _query_rows(con, sql):
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, (round(v, 4) if isinstance(v, float) else v for v in row))) for row in cursor.fetchall()]


def _analytics(con):
    """The numbers behind an upload's Overview tab, from SQL over its own table."""
    q = lambda sql: _query_rows(con, sql)
    return {
        "overview": q("""SELECT count(*) AS transactions, sum(amount) AS total_amount, count(model_flag) AS scored,
                                coalesce(sum(model_flag::INT), 0) AS flagged,
                                coalesce(sum(CASE WHEN model_flag THEN amount END), 0) AS flagged_amount,
                                count(DISTINCT name_orig) AS senders, count(DISTINCT name_dest) AS receivers, max(day) AS days,
                                sum(is_fraud) AS labelled_fraud, sum(CASE WHEN is_fraud = 1 THEN amount END) AS labelled_fraud_amount
                         FROM transactions""")[0],
        "type_summary": q("""SELECT type, count(*) AS transactions, sum(amount) AS total_amount, avg(amount) AS avg_amount,
                                    count(model_flag) AS scored, coalesce(sum(model_flag::INT), 0) AS flagged, sum(is_fraud) AS labelled_fraud
                             FROM transactions GROUP BY type ORDER BY transactions DESC"""),
        "hourly": q("""SELECT hour_of_day, count(*) AS transactions, count(model_flag) AS scored,
                              coalesce(sum(model_flag::INT), 0) AS flagged, sum(is_fraud) AS labelled_fraud
                       FROM transactions GROUP BY hour_of_day ORDER BY hour_of_day"""),
        "amount_bands": q("""SELECT LEAST(GREATEST(FLOOR(LOG10(amount))::INT, 1), 7) AS band, count(*) AS transactions,
                                    coalesce(sum(model_flag::INT), 0) AS flagged
                             FROM transactions GROUP BY band ORDER BY band"""),
        "score_histogram": q("""SELECT LEAST(FLOOR(fraud_probability * 10)::INT, 9) AS bin, count(*) AS transactions,
                                       sum(is_fraud) AS labelled_fraud
                                FROM transactions WHERE fraud_probability IS NOT NULL GROUP BY bin ORDER BY bin"""),
        "top_flagged_receivers": q("""SELECT name_dest, count(*) AS flagged, sum(amount) AS total_amount
                                      FROM transactions WHERE model_flag GROUP BY name_dest ORDER BY total_amount DESC LIMIT 10"""),
    }


def _index_upload(batch_id):
    """Loads the scored CSV into a DuckDB file with indexes (so the Explorer can search it) and returns its analytics."""
    building = UPLOAD_DIR / f"{batch_id}.building"
    for stale in UPLOAD_DIR.glob(f"{batch_id}.building*"):
        stale.unlink(missing_ok=True)
    con = duckdb.connect(str(building))
    try:
        con.execute("PRAGMA memory_limit='1GB'")
        con.execute(f"""
            CREATE TABLE transactions AS
            SELECT "row" AS tx_id, step, CASE WHEN step IS NULL THEN NULL ELSE (step - 1) // 24 + 1 END AS day, hour_of_day, type, amount,
                   nameOrig AS name_orig, oldbalanceOrg AS old_balance_orig, newbalanceOrig AS new_balance_orig,
                   nameDest AS name_dest, oldbalanceDest AS old_balance_dest, newbalanceDest AS new_balance_dest,
                   isFraud AS is_fraud, 0 AS is_flagged_fraud, fraud_probability, (model_flag = 'true') AS model_flag
            FROM read_csv('{_file_path(batch_id).as_posix()}', header = true, columns = {SCORED_COLUMNS})""")
        con.execute("CREATE UNIQUE INDEX idx_id ON transactions(tx_id)")
        con.execute("CREATE INDEX idx_orig ON transactions(name_orig)")
        con.execute("CREATE INDEX idx_dest ON transactions(name_dest)")
        analytics = _analytics(con)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(building, _db_path(batch_id))
    return analytics


# ---------------------------------------------------------------------------------------------
# the background job
# ---------------------------------------------------------------------------------------------
def _row_numbers(first_index, count, skipped):
    """File row numbers (header excluded) of parsed rows, counting malformed rows that were skipped."""
    numbers = np.arange(first_index + 1, first_index + count + 1, dtype=np.int64)
    for s in sorted(skipped):
        numbers = numbers + (numbers >= s)
    return numbers


def _run_job(batch_id, src, names, mapping, estimated_rows):
    batches = _ensure_indexes()
    oid = ObjectId(batch_id)
    cancel = _running[batch_id]
    out_part = UPLOAD_DIR / f"{batch_id}.part"
    started = time.perf_counter()
    acc = {"valid": 0, "invalid": 0, "scored": 0, "flagged": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "errors": [], "top": []}
    skipped, writer_holder = [], [None]
    parsed, last_update = 0, 0.0
    try:
        def on_bad_row(row):
            if row.number:
                skipped.append(row.number - 1)  # line 1 is the header
            return "skip"

        with open(src, "rb") as f:
            if f.read(3) != BOM:
                f.seek(0)
            reader = pacsv.open_csv(
                f, read_options=pacsv.ReadOptions(use_threads=False, block_size=BLOCK_SIZE),
                parse_options=pacsv.ParseOptions(invalid_row_handler=on_bad_row),
                convert_options=pacsv.ConvertOptions(column_types={n: pa.string() for n in names}, strings_can_be_null=False))
            for batch in reader:
                if cancel.is_set():
                    return
                _process_block(batch, mapping, _row_numbers(parsed, batch.num_rows, skipped), acc, writer_holder, out_part)
                parsed += batch.num_rows
                if time.time() - last_update > 0.3:
                    last_update = time.time()
                    updated = batches.update_one({"_id": oid}, {"$set": {
                        "rows_processed": parsed, "progress": round(min(0.88, parsed / max(estimated_rows, 1) * 0.88), 3)}})
                    if updated.matched_count == 0:  # the user deleted the upload while it was running
                        return
        if writer_holder[0] is not None:
            writer_holder[0].close()
            writer_holder[0] = None
        if acc["valid"] == 0:
            first = sorted(acc["errors"] + [(s, "row has the wrong number of columns") for s in sorted(skipped)[:1]])
            raise UploadError(f"No valid rows. First problem: row {first[0][0]}: {first[0][1]}" if first else "No valid rows")

        errors = sorted(acc["errors"] + [(s, "row has the wrong number of columns") for s in sorted(skipped)[:MAX_ERRORS_SHOWN]])
        tp, fp, fn, tn = acc["tp"], acc["fp"], acc["fn"], acc["tn"]
        labelled = "is_fraud" in mapping and (tp + fp + fn + tn) > 0
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
        rnd = lambda value: None if value is None else round(value, 4)
        elapsed = time.perf_counter() - started
        os.replace(out_part, _file_path(batch_id))
        batches.update_one({"_id": oid}, {"$set": {"phase": "indexing", "progress": 0.9}})
        index_started = time.perf_counter()
        analytics = _index_upload(batch_id)
        indexing_ms = round((time.perf_counter() - index_started) * 1000, 1)
        batches.update_one({"_id": oid}, {"$set": {
            "status": "done", "phase": "done", "progress": 1.0, "rows_processed": parsed + len(skipped),
            "indexing_ms": indexing_ms, "analytics": analytics,
            "rows_total": parsed + len(skipped), "rows_valid": acc["valid"], "rows_invalid": acc["invalid"] + len(skipped),
            "rows_scored": acc["scored"], "rows_not_modelled": acc["valid"] - acc["scored"], "flagged": acc["flagged"],
            "processing_ms": round(elapsed * 1000, 1), "rows_per_second": int((parsed + len(skipped)) / max(elapsed, 1e-9)),
            "errors": [{"row": n, "message": m} for n, m in errors[:MAX_ERRORS_SHOWN]],
            "metrics": {"rows": tp + fp + fn + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                        "precision": rnd(precision), "recall": rnd(recall), "f1": rnd(f1)} if labelled else None,
            "flagged_shown": len(acc["top"]), "flagged_rows": acc["top"]}})
    except UploadError as err:
        batches.update_one({"_id": oid}, {"$set": {"status": "failed", "error": str(err)}})
    except Exception as err:  # never leave a batch stuck in "processing"
        batches.update_one({"_id": oid}, {"$set": {"status": "failed", "error": f"Processing failed ({type(err).__name__}). Check that the file is a valid CSV."}})
    finally:
        if writer_holder[0] is not None:
            writer_holder[0].close()
        Path(src).unlink(missing_ok=True)
        if cancel.is_set() or batches.count_documents({"_id": oid}) == 0:
            out_part.unlink(missing_ok=True)
            _file_path(batch_id).unlink(missing_ok=True)
            _close_dataset_connection(batch_id)
            for leftover in UPLOAD_DIR.glob(f"{batch_id}.*"):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
        else:
            out_part.unlink(missing_ok=True)
        with _running_lock:
            _running.pop(batch_id, None)


# ---------------------------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------------------------
@router.get("/sample.csv")
def sample_file():
    path = RESULTS_DIR / "sample_upload.csv"
    if not path.exists():
        raise HTTPException(404, "Sample file not found")
    return FileResponse(path, media_type="text/csv", filename="sample_transactions.csv")


@router.post("", status_code=202)
def upload(file: UploadFile = File(...), owner: str = Depends(db.current_owner)):
    """Accepts the file and starts processing it. Poll GET /api/uploads/{id} for progress and the result."""
    batches = _ensure_indexes()
    if batches.count_documents({"owner": owner}) >= MAX_BATCHES_PER_OWNER:
        raise HTTPException(409, f"Limit of {MAX_BATCHES_PER_OWNER} uploads reached. Delete one to upload another.")

    _remove_old_files()
    batch_id = str(ObjectId())
    src = UPLOAD_DIR / f"{batch_id}.upload"
    size = _save_limited(file.file, src)
    try:
        names, mapping, estimated_rows = _inspect(src)
    except Exception:
        src.unlink(missing_ok=True)
        raise

    doc = {"_id": ObjectId(batch_id), "owner": owner, "filename": (file.filename or "upload.csv")[:120], "created_at": _now(),
           "size_bytes": size, "status": "processing", "phase": "scoring", "progress": 0.0, "rows_processed": 0, "rows_estimated": estimated_rows,
           "ignored_columns": [str(c)[:40] for c in names if c not in mapping.values()][:20]}
    batches.insert_one(doc)
    with _running_lock:
        _running[batch_id] = threading.Event()
    response = _public(doc)  # built before the job starts: a tiny file can finish before we return
    _jobs.submit(_run_job, batch_id, src, names, mapping, estimated_rows)
    return response


@router.get("")
def list_uploads(owner: str = Depends(db.current_owner)):
    batches = _ensure_indexes()
    return {"items": [_public(d) for d in batches.find({"owner": owner}).sort("created_at", -1)],
            "limit": MAX_BATCHES_PER_OWNER, "max_rows": MAX_ROWS, "max_bytes": MAX_BYTES}


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
    if doc["status"] != "done":
        raise HTTPException(409, "The file is not scored yet" if doc["status"] == "processing" else "This upload failed, there is no scored file")
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
    event = _running.get(batch_id)
    if event is not None:
        event.set()  # the running job stops at its next block and removes its files
    _close_dataset_connection(batch_id)
    for f in UPLOAD_DIR.glob(f"{batch_id}.*"):
        try:
            f.unlink(missing_ok=True)
        except OSError:  # a running job still has it open (Windows); the job removes it when it stops
            pass
    return Response(status_code=204)
