"""Step 2 - Descriptive analytics over the Parquet lake with Spark SQL.

Each query result is published to MongoDB (one collection per analysis), which
the dashboard API reads from.

Run: python -m spark_jobs.analytics
"""
from config import LAKE_DIR
from spark_jobs.common import get_spark, publish, to_records

QUERIES = {
    "overview": """
        SELECT COUNT(*)                                   AS transactions,
               ROUND(SUM(amount), 2)                      AS total_amount,
               SUM(is_fraud)                              AS fraud_transactions,
               ROUND(SUM(CASE WHEN is_fraud = 1 THEN amount ELSE 0 END), 2) AS fraud_amount,
               ROUND(100 * AVG(is_fraud), 4)              AS fraud_rate_pct,
               SUM(is_flagged_fraud)                      AS flagged_by_existing_rule,
               SUM(CASE WHEN is_flagged_fraud = 1 AND is_fraud = 1 THEN 1 ELSE 0 END) AS flagged_and_fraud,
               COUNT(DISTINCT name_orig)                  AS origin_accounts,
               COUNT(DISTINCT name_dest)                  AS destination_accounts,
               MAX(day)                                   AS days_covered
        FROM tx
    """,

    "type_summary": """
        SELECT type,
               COUNT(*)                         AS transactions,
               ROUND(SUM(amount), 2)            AS total_amount,
               ROUND(AVG(amount), 2)            AS avg_amount,
               SUM(is_fraud)                    AS fraud_transactions,
               ROUND(100 * AVG(is_fraud), 4)    AS fraud_rate_pct
        FROM tx
        GROUP BY type
        ORDER BY transactions DESC
    """,

    # fraudsters operate at a constant rate; legitimate activity follows a daily cycle
    "hourly": """
        SELECT hour_of_day,
               COUNT(*)                         AS transactions,
               SUM(is_fraud)                    AS fraud_transactions,
               ROUND(100 * AVG(is_fraud), 3)    AS fraud_rate_pct
        FROM tx
        GROUP BY hour_of_day
        ORDER BY hour_of_day
    """,

    "daily": """
        SELECT day,
               COUNT(*)                         AS transactions,
               SUM(is_fraud)                    AS fraud_transactions,
               ROUND(SUM(amount), 2)            AS total_amount
        FROM tx
        GROUP BY day
        ORDER BY day
    """,

    # share of each class (legit / fraud) falling in each order-of-magnitude amount band
    "amount_bands": """
        WITH banded AS (
            SELECT is_fraud,
                   LEAST(GREATEST(FLOOR(LOG10(amount)), 1), 7) AS band
            FROM tx
            WHERE type IN ('TRANSFER', 'CASH_OUT')
        )
        SELECT band,
               SUM(CASE WHEN is_fraud = 0 THEN 1 ELSE 0 END) AS legit,
               SUM(CASE WHEN is_fraud = 1 THEN 1 ELSE 0 END) AS fraud
        FROM banded
        GROUP BY band
        ORDER BY band
    """,

    "balance_patterns": """
        SELECT CASE WHEN is_fraud = 1 THEN 'fraud' ELSE 'legit' END AS class,
               COUNT(*) AS transactions,
               ROUND(100 * AVG(orig_emptied), 2) AS pct_origin_emptied,
               ROUND(100 * AVG(CASE WHEN old_balance_dest = 0 AND new_balance_dest = 0 THEN 1 ELSE 0 END), 2)
                   AS pct_dest_balance_unchanged_zero,
               ROUND(100 * AVG(CASE WHEN ABS(error_balance_orig) > 0.01 THEN 1 ELSE 0 END), 2)
                   AS pct_origin_balance_mismatch,
               ROUND(AVG(amount), 2) AS avg_amount,
               ROUND(PERCENTILE_APPROX(amount, 0.5), 2) AS median_amount,
               SUM(CASE WHEN amount = 10000000 THEN 1 ELSE 0 END) AS at_10m_cap
        FROM tx
        WHERE type IN ('TRANSFER', 'CASH_OUT')
        GROUP BY is_fraud
    """,

    # destination accounts that received more than one fraudulent transfer (possible mule accounts)
    "repeat_fraud_destinations": """
        SELECT name_dest,
               COUNT(*)                  AS fraud_transactions,
               ROUND(SUM(amount), 2)     AS total_amount,
               MIN(step)                 AS first_step,
               MAX(step)                 AS last_step
        FROM tx
        WHERE is_fraud = 1
        GROUP BY name_dest
        HAVING COUNT(*) > 1
        ORDER BY total_amount DESC
    """,
}


def main():
    spark = get_spark("paysim-analytics")
    spark.read.parquet(str(LAKE_DIR)).createOrReplaceTempView("tx")
    spark.catalog.cacheTable("tx")

    for name, sql in QUERIES.items():
        publish(name, to_records(spark.sql(sql)))

    spark.stop()


if __name__ == "__main__":
    main()
