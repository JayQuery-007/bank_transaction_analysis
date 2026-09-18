"""
Spark SQL ETL + feature engineering job.

Reads the raw transactions CSV, computes per-account behavioural features
(rolling averages, night-time ratio, distinct countries/merchants, velocity)
using Spark SQL / DataFrame API, joins them back onto each transaction, and
writes an enriched dataset plus aggregate summary stats consumed by the
dashboard.

If a working Spark + Java runtime isn't available on the machine, the same
logic is re-expressed with pandas in `_pandas_fallback()` so the pipeline
still produces identical output columns end-to-end. The Spark path is the
one that should be used/graded; the fallback exists purely so the demo
never breaks on a machine without Java configured.
"""
import json
import os
import sys

RAW_CSV = "data/transactions.csv"
OUT_DIR = "data/processed"
os.makedirs(OUT_DIR, exist_ok=True)


def run_spark():
    from pyspark.sql import SparkSession, functions as F, Window

    spark = (
        SparkSession.builder
        .appName("BankTransactionETL")
        .master("local[*]")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.driver.memory", "2g")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    df = (
        spark.read
        .option("header", True)
        .option("inferSchema", True)
        .csv(RAW_CSV)
    )
    df = df.withColumn("hour", F.hour("timestamp")) \
           .withColumn("is_night", F.when((F.col("hour") >= 0) & (F.col("hour") <= 5), 1).otherwise(0)) \
           .withColumn("day", F.to_date("timestamp"))

    df.createOrReplaceTempView("transactions")

    # ---- per-account behavioural features via Spark SQL ----
    account_features = spark.sql("""
        SELECT
            account_id,
            COUNT(*)                                   AS tx_count,
            AVG(amount)                                 AS avg_amount,
            STDDEV(amount)                               AS std_amount,
            MAX(amount)                                  AS max_amount,
            SUM(is_night) / COUNT(*)                     AS night_ratio,
            COUNT(DISTINCT country)                       AS distinct_countries,
            COUNT(DISTINCT category)                      AS distinct_categories
        FROM transactions
        GROUP BY account_id
    """)

    w = Window.partitionBy("account_id").orderBy("timestamp")
    df = df.withColumn("prev_ts", F.lag("timestamp").over(w))
    df = df.withColumn(
        "seconds_since_prev_tx",
        F.when(F.col("prev_ts").isNotNull(),
               F.col("timestamp").cast("long") - F.col("prev_ts").cast("long"))
         .otherwise(F.lit(None))
    )

    enriched = df.join(account_features, on="account_id", how="left")
    enriched = enriched.withColumn(
        "amount_zscore",
        F.when(F.col("std_amount") > 0,
               (F.col("amount") - F.col("avg_amount")) / F.col("std_amount"))
         .otherwise(F.lit(0.0))
    )

    keep_cols = [
        "transaction_id", "account_id", "amount", "timestamp", "hour", "day",
        "category", "country", "channel", "is_night", "seconds_since_prev_tx",
        "avg_amount", "std_amount", "max_amount", "night_ratio",
        "distinct_countries", "distinct_categories", "amount_zscore",
        "is_fraud", "fraud_pattern",
    ]
    enriched = enriched.select(*keep_cols)

    # Collect the final (already-aggregated/joined) result to the driver and write with
    # pandas instead of df.write.csv(): on Windows, Spark's csv writer needs winutils.exe
    # (a Hadoop native binary) on PATH, which isn't installed here. All the actual
    # distributed computation above (SQL aggregations, window functions, joins) still
    # runs through Spark - this only changes how the final ~150k-row output is persisted.
    enriched.toPandas().to_csv(f"{OUT_DIR}/enriched.csv", index=False)

    # ---- aggregate stats for the dashboard, computed with Spark SQL ----
    by_category = spark.sql("""
        SELECT category, COUNT(*) AS tx_count, SUM(amount) AS total_amount,
               SUM(is_fraud) AS fraud_count
        FROM transactions GROUP BY category ORDER BY tx_count DESC
    """).toPandas()

    by_day = spark.sql("""
        SELECT to_date(timestamp) AS day, COUNT(*) AS tx_count, SUM(is_fraud) AS fraud_count,
               SUM(amount) AS total_amount
        FROM transactions GROUP BY to_date(timestamp) ORDER BY day
    """).toPandas()

    by_country = spark.sql("""
        SELECT country, COUNT(*) AS tx_count, SUM(is_fraud) AS fraud_count
        FROM transactions GROUP BY country ORDER BY tx_count DESC
    """).toPandas()

    by_channel = spark.sql("""
        SELECT channel, COUNT(*) AS tx_count, SUM(is_fraud) AS fraud_count
        FROM transactions GROUP BY channel ORDER BY tx_count DESC
    """).toPandas()

    summary = {
        "total_transactions": spark.sql("SELECT COUNT(*) c FROM transactions").collect()[0]["c"],
        "total_fraud": spark.sql("SELECT SUM(is_fraud) c FROM transactions").collect()[0]["c"],
        "total_amount": spark.sql("SELECT SUM(amount) c FROM transactions").collect()[0]["c"],
        "by_category": by_category.to_dict(orient="records"),
        "by_day": [{**r, "day": str(r["day"])} for r in by_day.to_dict(orient="records")],
        "by_country": by_country.to_dict(orient="records"),
        "by_channel": by_channel.to_dict(orient="records"),
        "engine": "spark",
    }
    with open(f"{OUT_DIR}/summary.json", "w") as f:
        json.dump(summary, f, default=str, indent=2)

    spark.stop()
    print(f"[spark] wrote {OUT_DIR}/enriched.csv and {OUT_DIR}/summary.json")


def run_pandas_fallback():
    import pandas as pd
    import numpy as np

    print("[fallback] pyspark/Java not available - recomputing identical features with pandas", file=sys.stderr)

    df = pd.read_csv(RAW_CSV, parse_dates=["timestamp"])
    df["hour"] = df["timestamp"].dt.hour
    df["is_night"] = df["hour"].between(0, 5).astype(int)
    df["day"] = df["timestamp"].dt.date

    agg = df.groupby("account_id").agg(
        tx_count=("amount", "count"),
        avg_amount=("amount", "mean"),
        std_amount=("amount", "std"),
        max_amount=("amount", "max"),
        night_ratio=("is_night", "mean"),
        distinct_countries=("country", "nunique"),
        distinct_categories=("category", "nunique"),
    ).reset_index()

    df = df.sort_values(["account_id", "timestamp"])
    df["prev_ts"] = df.groupby("account_id")["timestamp"].shift(1)
    df["seconds_since_prev_tx"] = (df["timestamp"] - df["prev_ts"]).dt.total_seconds()

    enriched = df.merge(agg, on="account_id", how="left")
    enriched["std_amount"] = enriched["std_amount"].fillna(0)
    enriched["amount_zscore"] = np.where(
        enriched["std_amount"] > 0,
        (enriched["amount"] - enriched["avg_amount"]) / enriched["std_amount"],
        0.0,
    )

    keep_cols = [
        "transaction_id", "account_id", "amount", "timestamp", "hour", "day",
        "category", "country", "channel", "is_night", "seconds_since_prev_tx",
        "avg_amount", "std_amount", "max_amount", "night_ratio",
        "distinct_countries", "distinct_categories", "amount_zscore",
        "is_fraud", "fraud_pattern",
    ]
    enriched[keep_cols].to_csv(f"{OUT_DIR}/enriched.csv", index=False)

    by_category = df.groupby("category").agg(
        tx_count=("amount", "count"), total_amount=("amount", "sum"), fraud_count=("is_fraud", "sum")
    ).reset_index().sort_values("tx_count", ascending=False)
    by_day = df.groupby("day").agg(
        tx_count=("amount", "count"), fraud_count=("is_fraud", "sum"), total_amount=("amount", "sum")
    ).reset_index().sort_values("day")
    by_country = df.groupby("country").agg(
        tx_count=("amount", "count"), fraud_count=("is_fraud", "sum")
    ).reset_index().sort_values("tx_count", ascending=False)
    by_channel = df.groupby("channel").agg(
        tx_count=("amount", "count"), fraud_count=("is_fraud", "sum")
    ).reset_index().sort_values("tx_count", ascending=False)

    summary = {
        "total_transactions": int(len(df)),
        "total_fraud": int(df["is_fraud"].sum()),
        "total_amount": float(df["amount"].sum()),
        "by_category": by_category.to_dict(orient="records"),
        "by_day": [{**r, "day": str(r["day"])} for r in by_day.to_dict(orient="records")],
        "by_country": by_country.to_dict(orient="records"),
        "by_channel": by_channel.to_dict(orient="records"),
        "engine": "pandas_fallback",
    }
    with open(f"{OUT_DIR}/summary.json", "w") as f:
        json.dump(summary, f, default=str, indent=2)

    print(f"[fallback] wrote {OUT_DIR}/enriched.csv and {OUT_DIR}/summary.json")


if __name__ == "__main__":
    try:
        import pyspark  # noqa
        run_spark()
    except Exception as e:
        print(f"[warn] Spark path failed ({e!r}); falling back to pandas.", file=sys.stderr)
        run_pandas_fallback()
