"""Prints what is stored where, for showing the databases in a demo.

Usage: python -m scripts.show_db
"""
import pprint

import duckdb

from config import LAKE_DIR, MONGO_DB, MONGO_URI, SEARCH_DB


def mongo():
    from pymongo import MongoClient

    print(f"== MongoDB {MONGO_URI}, database '{MONGO_DB}' ==")
    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)[MONGO_DB]
    try:
        names = sorted(db.list_collection_names())
    except Exception as err:
        print(f"MongoDB is not reachable: {err}")
        return
    for name in names:
        print(f"  {name:28} {db[name].count_documents({}):>6} documents")
    for name in ("cases", "upload_batches"):
        print(f"\nindexes on {name}:")
        for index in db[name].list_indexes():
            print("  ", index["name"], dict(index["key"]), {k: v for k, v in index.items() if k in ("unique", "expireAfterSeconds", "partialFilterExpression")})
    case = db["cases"].find_one({}, {"_id": 0})
    if case:
        print("\none case document:")
        pprint.pprint(case, width=110)


def search_index():
    print(f"\n== DuckDB search index {SEARCH_DB} ==")
    if not SEARCH_DB.exists():
        print("not built yet: python -m serving.build_search_index")
        return
    con = duckdb.connect(str(SEARCH_DB), read_only=True)
    print("rows:", con.execute("select count(*) from transactions").fetchone()[0])
    print("indexes:", [r[0] for r in con.execute("select index_name from duckdb_indexes()").fetchall()])
    print("first rows:")
    for row in con.execute("select tx_id, type, amount, name_orig, name_dest, is_fraud from transactions limit 3").fetchall():
        print("  ", row)


def lake():
    print(f"\n== Parquet data lake {LAKE_DIR} ==")
    if not LAKE_DIR.exists():
        print("not built yet: python -m spark_jobs.ingest")
        return
    pattern = (LAKE_DIR / "*" / "*.parquet").as_posix()
    rows = duckdb.sql(f"select type, count(*), sum(is_fraud) from read_parquet('{pattern}', hive_partitioning=true) "
                      f"group by type order by 2 desc").fetchall()
    print(f"  {'type':10} {'transactions':>13} {'fraud':>7}")
    for kind, n, fraud in rows:
        print(f"  {kind:10} {n:>13,} {fraud:>7,}")


if __name__ == "__main__":
    mongo()
    search_index()
    lake()
