"""Millisecond transaction search over the DuckDB index built by serving/build_search_index.py.

All values reach DuckDB as bound parameters. The only text spliced into SQL comes from
fixed whitelists (sort column, sort direction), never from the request.
"""
import os
import re
import threading
import time
from typing import Literal, Optional

import duckdb
from fastapi import APIRouter, Header, HTTPException, Query

from config import SEARCH_DB

from . import db, forest, uploads

router = APIRouter(prefix="/api", tags=["search"])

TYPES = ("CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER")
MODELLED_TYPES = ("TRANSFER", "CASH_OUT")  # the only types with fraud in the training data
COLUMNS = ("tx_id", "step", "day", "hour_of_day", "type", "amount", "name_orig", "old_balance_orig",
           "new_balance_orig", "name_dest", "old_balance_dest", "new_balance_dest", "is_fraud",
           "is_flagged_fraud")
SORTABLE = {"tx_id": "tx_id", "amount": "amount", "step": "step"}
# an uploaded file is scored when it is processed, so its table also holds the model's verdict
UPLOAD_COLUMNS = COLUMNS + ("fraud_probability", "model_flag")
UPLOAD_SORTABLE = {**SORTABLE, "fraud_probability": "fraud_probability"}
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_lock = threading.Lock()
_con = None
_fetch = {"state": "idle", "error": None}  # idle | downloading | failed


def index_available():
    return SEARCH_DB.exists()


def index_state():
    """ready | downloading | failed | missing, for the health check and the Explorer's message."""
    if SEARCH_DB.exists():
        return "ready"
    return _fetch["state"] if _fetch["state"] != "idle" else "missing"


def start_fetch():
    """Hosted containers have no data lake: download the prebuilt index in the background so the
    server starts at once and the Explorer says "downloading" until it is there."""
    if SEARCH_DB.exists() or not os.environ.get("SEARCH_INDEX_REPO") or _fetch["state"] == "downloading":
        return
    _fetch.update(state="downloading", error=None)

    def work():
        try:
            from serving import fetch_index

            fetch_index.fetch()
            warm_up()
            _fetch["state"] = "idle"
        except Exception as err:  # reported through /api/health
            _fetch.update(state="failed", error=f"{type(err).__name__}: {str(err)[:200]}")

    threading.Thread(target=work, daemon=True, name="fetch-search-index").start()


def _connection():
    global _con
    with _lock:
        if _con is None:
            if not SEARCH_DB.exists():
                if _fetch["state"] == "downloading":
                    raise HTTPException(503, "The search index is still downloading. Try again in a minute.")
                raise HTTPException(503, "Search index not built. Run: python -m serving.build_search_index")
            con = duckdb.connect(str(SEARCH_DB), read_only=True)
            con.execute(f"PRAGMA memory_limit='{os.environ.get('SEARCH_MEMORY_LIMIT', '1GB')}'")
            # let the optimiser use the account indexes even for accounts with many rows
            con.execute("SET GLOBAL index_scan_max_count = 100000")
            _con = con
        return _con


def warm_up():
    """Touch the table and both account indexes so the first real query isn't the slow one."""
    if not index_available():
        return
    cur = _connection().cursor()
    cur.execute("SELECT name_orig, name_dest FROM transactions WHERE tx_id = 1").fetchall()
    for column in ("name_orig", "name_dest"):
        account = cur.execute(f"SELECT {column} FROM transactions WHERE tx_id = 1000").fetchone()[0]
        cur.execute(f"SELECT * FROM transactions WHERE {column} = ? LIMIT 5", [account]).fetchall()
    cur.close()


def status():
    if not index_available():
        return None
    cur = _connection().cursor()
    rows = cur.execute("SELECT count(*) FROM transactions").fetchone()[0]
    cur.close()
    return {"rows": rows, "size_mb": round(SEARCH_DB.stat().st_size / 1e6), "engine": f"duckdb {duckdb.__version__}"}


def _score(rows):
    """Adds the model's fraud probability to rows of a type the model was trained on."""
    idx = [i for i, r in enumerate(rows) if r["type"] in MODELLED_TYPES]
    for r in rows:
        r["fraud_probability"] = None
        r["model_flag"] = None
    if not idx:
        return rows
    x = forest.feature_matrix(
        [1 if rows[i]["type"] == "TRANSFER" else 0 for i in idx], [rows[i]["amount"] for i in idx],
        [rows[i]["old_balance_orig"] for i in idx], [rows[i]["new_balance_orig"] for i in idx],
        [rows[i]["old_balance_dest"] for i in idx], [rows[i]["new_balance_dest"] for i in idx],
        [rows[i]["hour_of_day"] for i in idx],
    )
    for i, p in zip(idx, forest.fraud_probability(x)):
        rows[i]["fraud_probability"] = round(float(p), 4)
        rows[i]["model_flag"] = bool(p > forest.THRESHOLD)
    return rows


class Target:
    """What one request searches: the built-in PaySim index, or one of the owner's uploaded files."""

    def __init__(self, con, columns, sortable, stored_scores):
        self.con, self.columns, self.sortable, self.stored_scores = con, columns, sortable, stored_scores


def _target(dataset, x_owner):
    if dataset in (None, "", "paysim"):
        return Target(_connection(), COLUMNS, SORTABLE, False)
    con, _batch = uploads.dataset_connection(dataset, db.current_owner(x_owner))
    return Target(con, UPLOAD_COLUMNS, UPLOAD_SORTABLE, True)


def _rows(cursor, columns):
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


@router.get("/search")
def search(
    account: Optional[str] = Query(None, description="Account id, e.g. C1305486145"),
    role: Literal["any", "sender", "receiver"] = "any",
    type: Optional[Literal["CASH_IN", "CASH_OUT", "DEBIT", "PAYMENT", "TRANSFER"]] = None,
    min_amount: Optional[float] = Query(None, ge=0),
    max_amount: Optional[float] = Query(None, ge=0),
    hour_from: Optional[int] = Query(None, ge=0, le=23),
    hour_to: Optional[int] = Query(None, ge=0, le=23),
    fraud: Optional[bool] = Query(None, description="Filter on the dataset's fraud label"),
    flagged: Optional[bool] = Query(None, description="Filter on the model's verdict (uploaded datasets only)"),
    sort: Literal["tx_id", "amount", "step", "fraud_probability"] = "tx_id",
    order: Literal["asc", "desc"] = "asc",
    skip: int = Query(0, ge=0, le=10000),
    limit: int = Query(25, ge=1, le=200),
    dataset: str = Query("paysim", description="'paysim' or the id of one of your uploads"),
    x_owner: Optional[str] = Header(None),
):
    if account is not None and not ACCOUNT_RE.match(account):
        raise HTTPException(422, "account must be 1-32 letters, digits, '_' or '-'")
    if hour_from is not None and hour_to is not None and hour_from > hour_to:
        raise HTTPException(422, "hour_from must not be greater than hour_to")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise HTTPException(422, "min_amount must not be greater than max_amount")
    target = _target(dataset, x_owner)
    if sort not in target.sortable:
        raise HTTPException(422, f"sort={sort} is only available for uploaded datasets")
    if flagged is not None and "model_flag" not in target.columns:
        raise HTTPException(422, "the flagged filter is only available for uploaded datasets")

    filters, fparams = [], []
    for clause, value in [("type = ?", type), ("amount >= ?", min_amount), ("amount <= ?", max_amount),
                          ("hour_of_day >= ?", hour_from), ("hour_of_day <= ?", hour_to),
                          ("is_fraud = ?", None if fraud is None else int(fraud)),
                          ("model_flag = ?", flagged)]:
        if value is not None:
            filters.append(clause)
            fparams.append(value)

    cur = target.con.cursor()
    started = time.perf_counter()
    if account:
        items, total = _search_account(cur, target, account, role, filters, fparams, sort, order, skip, limit)
    else:
        items, total = _search_scan(cur, target, filters, fparams, sort, order, skip, limit)
    took_ms = (time.perf_counter() - started) * 1000
    cur.close()

    return {
        "items": items if target.stored_scores else _score(items), "total": total, "skip": skip, "limit": limit,
        "took_ms": round(took_ms, 2), "access": "index" if account else "scan",
    }


ACCOUNT_ROW_CAP = 5000  # PaySim's busiest account has 113 rows; the cap only guards against odd data


def _search_account(cur, target, account, role, filters, fparams, sort, order, skip, limit):
    """Index lookups on the sender and/or receiver column, then merge and sort the few rows in Python.

    Doing the ORDER BY in SQL makes DuckDB drop the account index (12x slower, measured), and
    a single OR across two indexed columns is slower again, so each side is looked up on its own.
    """
    extra = "".join(f" AND {f}" for f in filters)
    sides = {"sender": "name_orig", "receiver": "name_dest"}
    rows = {}
    for side, column in sides.items():
        if role in ("any", side):
            sql = f"SELECT {', '.join(target.columns)} FROM transactions WHERE {column} = ?{extra} LIMIT ?"
            for row in _rows(cur.execute(sql, [account, *fparams, ACCOUNT_ROW_CAP]), target.columns):
                rows[row["tx_id"]] = row
    key = target.sortable[sort]
    ordered = sorted(rows.values(), key=lambda r: r["tx_id"])
    present = [r for r in ordered if r[key] is not None]  # rows without a value (e.g. an unscored type) always come last
    present.sort(key=lambda r: r[key], reverse=order == "desc")
    ordered = present + [r for r in ordered if r[key] is None]
    return ordered[skip: skip + limit], len(ordered)


def _search_scan(cur, target, filters, fparams, sort, order, skip, limit):
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    order_sql = f"ORDER BY {target.sortable[sort]} {'DESC' if order == 'desc' else 'ASC'} NULLS LAST, tx_id"
    total = cur.execute(f"SELECT count(*) FROM transactions {where_sql}", fparams).fetchone()[0]
    items = _rows(cur.execute(
        f"SELECT {', '.join(target.columns)} FROM transactions {where_sql} {order_sql} LIMIT ? OFFSET ?",
        fparams + [limit, skip]), target.columns)
    return items, total


def fetch_transaction(tx_id, dataset="paysim", owner=None):
    """One transaction with its model score, or None. Also used when a case is opened."""
    target = _target(dataset, owner)
    cur = target.con.cursor()
    started = time.perf_counter()
    rows = _rows(cur.execute(f"SELECT {', '.join(target.columns)} FROM transactions WHERE tx_id = ?", [tx_id]), target.columns)
    took_ms = (time.perf_counter() - started) * 1000
    cur.close()
    if not rows:
        return None
    return {**(rows if target.stored_scores else _score(rows))[0], "took_ms": round(took_ms, 2)}


def _prefix_end(prefix):
    """Smallest string greater than every string that starts with prefix (for an index range scan)."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


@router.get("/account-suggestions")
def account_suggestions(
    prefix: str = Query(..., description="The first characters of an account id"),
    limit: int = Query(10, ge=1, le=20),
    dataset: str = Query("paysim"),
    x_owner: Optional[str] = Header(None),
):
    """Accounts whose id starts with prefix, in id order: range scans on the two account indexes."""
    prefix = prefix.strip().upper()
    if not ACCOUNT_RE.match(prefix):
        raise HTTPException(422, "prefix must be 1-32 letters, digits, '_' or '-'")
    cur = _target(dataset, x_owner).con.cursor()
    started = time.perf_counter()
    found = set()
    for column in ("name_orig", "name_dest"):
        rows = cur.execute(f"SELECT {column} FROM transactions WHERE {column} >= ? AND {column} < ? LIMIT ?",
                           [prefix, _prefix_end(prefix), limit * 3]).fetchall()
        found.update(r[0] for r in rows)
    cur.close()
    items = sorted(found)[:limit]
    return {"prefix": prefix, "items": items, "took_ms": round((time.perf_counter() - started) * 1000, 2)}


@router.get("/transactions/{tx_id}")
def get_transaction(tx_id: int, dataset: str = "paysim", x_owner: Optional[str] = Header(None)):
    row = fetch_transaction(tx_id, dataset, x_owner)
    if row is None:
        raise HTTPException(404, f"Transaction {tx_id} not found")
    return row


@router.get("/accounts/{account_id}")
def account_summary(account_id: str, dataset: str = "paysim", x_owner: Optional[str] = Header(None)):
    if not ACCOUNT_RE.match(account_id):
        raise HTTPException(422, "invalid account id")
    cur = _target(dataset, x_owner).con.cursor()
    started = time.perf_counter()
    sent = cur.execute(
        "SELECT count(*), coalesce(sum(amount), 0), coalesce(sum(is_fraud), 0), min(step), max(step) "
        "FROM transactions WHERE name_orig = ?", [account_id]).fetchone()
    received = cur.execute(
        "SELECT count(*), coalesce(sum(amount), 0), coalesce(sum(is_fraud), 0), min(step), max(step) "
        "FROM transactions WHERE name_dest = ?", [account_id]).fetchone()
    took_ms = (time.perf_counter() - started) * 1000
    cur.close()
    if sent[0] == 0 and received[0] == 0:
        raise HTTPException(404, f"Account {account_id} not found")

    def side(row):
        return {"transactions": row[0], "total_amount": round(row[1], 2), "fraud_labelled": int(row[2]),
                "first_step": row[3], "last_step": row[4]}

    return {"account": account_id, "kind": "merchant" if account_id.startswith("M") else "customer",
            "as_sender": side(sent), "as_receiver": side(received), "took_ms": round(took_ms, 2)}
