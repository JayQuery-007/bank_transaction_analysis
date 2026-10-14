"""Times the same account lookup four ways and writes results/search_benchmark.json.

  1. Spark SQL full scan of the Parquet lake      (JVM already warm, startup excluded)
  2. DuckDB straight on the Parquet lake          (no table, no index)
  3. DuckDB table with the index switched off     (columnar scan of the indexed table)
  4. DuckDB table with the ART index              (what the search API uses)

Run: python -m scripts.benchmark_search [--skip-spark]
"""
import argparse
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone

import duckdb

from config import LAKE_DIR, SEARCH_DB

ACCOUNTS = 30
SPARK_ACCOUNTS = 5  # each Spark scan takes seconds


def summarise(name, method, samples_ms):
    ordered = sorted(samples_ms)
    return {
        "name": name, "method": method, "runs": len(ordered),
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        "max_ms": round(ordered[-1], 2),
    }


def timed(fn, accounts):
    samples = []
    for account in accounts:
        start = time.perf_counter()
        fn(account)
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-spark", action="store_true")
    args = parser.parse_args()

    lake_glob = (LAKE_DIR / "*" / "*.parquet").as_posix()
    con = duckdb.connect(str(SEARCH_DB), read_only=True)
    con.execute("PRAGMA memory_limit='2GB'")
    accounts = [r[0] for r in con.execute(
        f"SELECT name_orig FROM transactions USING SAMPLE reservoir({ACCOUNTS} ROWS) REPEATABLE (42)").fetchall()]
    rows = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    lake_cols = "step, type, amount, name_orig, name_dest, is_fraud"  # the lake has no tx_id; the index adds it
    lookup = f"SELECT {lake_cols} FROM transactions WHERE name_orig = ?"

    engines = []

    if not args.skip_spark:
        from spark_jobs.common import get_spark

        spark = get_spark("search-benchmark")
        lake = spark.read.parquet(str(LAKE_DIR))
        lake.createOrReplaceTempView("lake")
        lake.limit(1).collect()  # warm the JVM and list files once
        query = "SELECT step, type, amount, name_orig, name_dest, is_fraud FROM lake WHERE name_orig = '{}'"
        samples = timed(lambda a: spark.sql(query.format(a)).collect(), accounts[:SPARK_ACCOUNTS])
        engines.append(summarise("Spark SQL", "full scan of the Parquet lake", samples))
        spark.stop()

    samples = timed(lambda a: con.execute(
        f"SELECT {lake_cols} FROM read_parquet('{lake_glob}', hive_partitioning = true) WHERE name_orig = ?", [a]).fetchall(),
        accounts[:10])
    engines.append(summarise("DuckDB on Parquet", "scan of the Parquet lake, no table or index", samples))

    con.execute("SET GLOBAL index_scan_max_count = 0")
    con.execute("SET GLOBAL index_scan_percentage = 0")
    samples = timed(lambda a: con.execute(lookup, [a]).fetchall(), accounts)
    engines.append(summarise("DuckDB table, index off", "columnar scan of the indexed table", samples))

    con.execute("RESET GLOBAL index_scan_percentage")
    con.execute("SET GLOBAL index_scan_max_count = 100000")
    con.execute(lookup, [accounts[0]]).fetchall()  # warm
    samples = timed(lambda a: con.execute(lookup, [a]).fetchall(), accounts)
    engines.append(summarise("DuckDB table, ART index", "index lookup (what the search API uses)", samples))

    fastest = engines[-1]["median_ms"]
    for e in engines:
        e["slowdown_vs_index"] = round(e["median_ms"] / fastest, 1)

    result = {
        "query": "all transactions sent by one account (name_orig = ?)",
        "rows_searched": rows, "accounts_tested": ACCOUNTS,
        "engines": engines,
        "environment": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "cpu_threads": os.cpu_count(), "os": platform.platform(), "python": platform.python_version(),
            "duckdb": duckdb.__version__,
        },
    }
    from spark_jobs.common import publish

    publish("search_benchmark", result)
    for e in engines:
        print(f"{e['name']:<26} median {e['median_ms']:>9.2f} ms   p95 {e['p95_ms']:>9.2f} ms   ({e['runs']} runs)  x{e['slowdown_vs_index']}")


if __name__ == "__main__":
    sys.exit(main())
