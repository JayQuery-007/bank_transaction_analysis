"""Step 1 - Ingest the raw PaySim CSV into a Parquet data lake.

- reads the CSV with an explicit schema (no inferSchema pass over 6M rows)
- runs basic data-quality checks
- renames columns to snake_case and adds derived columns used later
- writes Parquet partitioned by transaction type

Run: python -m spark_jobs.ingest
"""
import time

from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType

from config import LAKE_DIR, RAW_CSV
from spark_jobs.common import get_spark, publish

RAW_SCHEMA = StructType([
    StructField("step", IntegerType()),
    StructField("type", StringType()),
    StructField("amount", DoubleType()),
    StructField("nameOrig", StringType()),
    StructField("oldbalanceOrg", DoubleType()),
    StructField("newbalanceOrig", DoubleType()),
    StructField("nameDest", StringType()),
    StructField("oldbalanceDest", DoubleType()),
    StructField("newbalanceDest", DoubleType()),
    StructField("isFraud", IntegerType()),
    StructField("isFlaggedFraud", IntegerType()),
])


def main():
    if not RAW_CSV.exists():
        raise SystemExit(f"{RAW_CSV} not found - run: python data/download_data.py")

    spark = get_spark("paysim-ingest")
    start = time.time()

    raw = spark.read.csv(str(RAW_CSV), header=True, schema=RAW_SCHEMA)

    df = raw.select(
        "step",
        "type",
        "amount",
        F.col("nameOrig").alias("name_orig"),
        F.col("oldbalanceOrg").alias("old_balance_orig"),
        F.col("newbalanceOrig").alias("new_balance_orig"),
        F.col("nameDest").alias("name_dest"),
        F.col("oldbalanceDest").alias("old_balance_dest"),
        F.col("newbalanceDest").alias("new_balance_dest"),
        F.col("isFraud").alias("is_fraud"),
        F.col("isFlaggedFraud").alias("is_flagged_fraud"),
    ).cache()

    # data quality checks
    total = df.count()
    null_counts = df.select([F.sum(F.col(c).isNull().cast("int")).alias(c) for c in df.columns]).first().asDict()
    quality = {
        "total_rows": total,
        "rows_with_nulls": int(sum(v or 0 for v in null_counts.values())),
        "negative_amounts": df.filter(F.col("amount") < 0).count(),
        "zero_amounts": df.filter(F.col("amount") == 0).count(),
        "duplicate_rows": total - df.dropDuplicates().count(),
        "distinct_origin_accounts": df.select("name_orig").distinct().count(),
        "distinct_destination_accounts": df.select("name_dest").distinct().count(),
    }
    print("Data quality:", quality)

    # step = 1 hour of simulated time, 744 steps = 31 days
    df = (
        df.filter(F.col("amount") > 0)
        .withColumn("day", ((F.col("step") - 1) / 24).cast("int") + 1)
        .withColumn("hour_of_day", F.col("step") % 24)
        .withColumn("dest_is_merchant", F.col("name_dest").startswith("M").cast("int"))
        # how far the recorded balances are from what the amount says they should be
        .withColumn("error_balance_orig", F.col("new_balance_orig") + F.col("amount") - F.col("old_balance_orig"))
        .withColumn("error_balance_dest", F.col("old_balance_dest") + F.col("amount") - F.col("new_balance_dest"))
        .withColumn(
            "orig_emptied",
            ((F.col("old_balance_orig") > 0) & (F.col("new_balance_orig") == 0)).cast("int"),
        )
    )

    (
        df.repartition("type")
        .write.mode("overwrite")
        .partitionBy("type")
        .parquet(str(LAKE_DIR))
    )

    written = spark.read.parquet(str(LAKE_DIR)).count()
    quality["rows_written"] = written
    quality["seconds"] = round(time.time() - start, 1)
    publish("data_quality", quality)

    print(f"Wrote {written:,} rows to {LAKE_DIR} in {quality['seconds']}s")
    spark.stop()


if __name__ == "__main__":
    main()
