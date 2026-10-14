"""Step 4 - Build the transaction search index.

Reads the Parquet lake written by spark_jobs/ingest.py with DuckDB (no copy through another
database) and stores it as one table with ART indexes on both account columns, so looking up a
sender, a receiver or a transaction id takes milliseconds instead of a scan.

tx_id is row_number() over a total ordering of every column, so the same lake always produces
the same ids (the dataset has no duplicate rows, checked by spark_jobs/ingest.py). Cases opened
in the dashboard refer to these ids.

Run: python -m serving.build_search_index
"""
import os
import time

import duckdb

from config import LAKE_DIR, SEARCH_DB

COLUMNS = """
    step, day, hour_of_day, type, amount,
    name_orig, old_balance_orig, new_balance_orig,
    name_dest, old_balance_dest, new_balance_dest,
    is_fraud, is_flagged_fraud
"""


def main():
    if not LAKE_DIR.exists():
        raise SystemExit(f"{LAKE_DIR} not found - run: python -m spark_jobs.ingest")

    tmp = SEARCH_DB.with_suffix(".building")
    for stale in SEARCH_DB.parent.glob(tmp.name + "*"):
        stale.unlink()
    SEARCH_DB.parent.mkdir(parents=True, exist_ok=True)

    start = time.time()
    con = duckdb.connect(str(tmp))
    con.execute("PRAGMA memory_limit='6GB'")
    lake = (LAKE_DIR / "*" / "*.parquet").as_posix()

    con.execute(f"""
        CREATE TABLE transactions AS
        SELECT row_number() OVER (ORDER BY step, name_orig, name_dest, type, amount,
                                  old_balance_orig, new_balance_orig,
                                  old_balance_dest, new_balance_dest) AS tx_id,
               {COLUMNS}
        FROM read_parquet('{lake}', hive_partitioning = true)
    """)
    rows = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    print(f"table: {rows:,} rows in {time.time() - start:.0f}s")

    for name, column in [("tx_id", "tx_id"), ("name_orig", "name_orig"), ("name_dest", "name_dest")]:
        t = time.time()
        unique = "UNIQUE " if column == "tx_id" else ""
        con.execute(f"CREATE {unique}INDEX idx_{name} ON transactions({column})")
        print(f"index on {column}: {time.time() - t:.0f}s")

    con.execute("CHECKPOINT")
    con.close()

    for stale in SEARCH_DB.parent.glob(SEARCH_DB.name + "*"):
        stale.unlink()
    os.replace(tmp, SEARCH_DB)
    print(f"wrote {SEARCH_DB} ({SEARCH_DB.stat().st_size / 1e6:.0f} MB) in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
