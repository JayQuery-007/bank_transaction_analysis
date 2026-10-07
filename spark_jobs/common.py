import json
import os
import shutil
import sys
from datetime import date, datetime

from config import MONGO_DB, MONGO_URI, RESULTS_DIR, ROOT

WINFS_JAR = ROOT / "spark_jobs" / "winfs" / "winfs.jar"


def _find_java():
    if os.environ.get("JAVA_HOME") or shutil.which("java"):
        return
    bundled = sorted((ROOT / "jdk").glob("jdk-*"))
    if bundled:
        os.environ["JAVA_HOME"] = str(bundled[-1])
        return
    sys.exit("Java not found. Install Java 17 and set JAVA_HOME (Spark needs it).")


def get_spark(app_name):
    _find_java()
    os.environ["PYSPARK_PYTHON"] = sys.executable

    from pyspark.sql import SparkSession

    builder = (
        SparkSession.builder.appName(app_name)
        .master("local[*]")
        .config("spark.driver.memory", "4g")
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.ui.showConsoleProgress", "false")
    )
    if os.name == "nt":
        # see spark_jobs/winfs/src - avoids needing winutils.exe/hadoop.dll on Windows
        builder = (
            builder.config("spark.driver.extraClassPath", str(WINFS_JAR))
            .config("spark.hadoop.fs.file.impl", "bda.WinLocalFileSystem")
        )

    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    raise TypeError(f"Cannot serialise {type(value)}")


def _clean(records):
    return json.loads(json.dumps(records, default=_json_default))


def publish(name, records):
    """Write a result set to MongoDB (serving layer) and to results/<name>.json."""
    if isinstance(records, dict):
        records = [records]
    records = _clean(records)

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(RESULTS_DIR / f"{name}.json", "w") as f:
        json.dump(records, f, indent=1)

    try:
        from pymongo import MongoClient

        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        coll = client[MONGO_DB][name]
        coll.delete_many({})
        if records:
            coll.insert_many([dict(r) for r in records])
        print(f"  -> {name}: {len(records)} docs written to MongoDB and results/{name}.json")
    except Exception as e:
        print(f"  -> {name}: written to results/{name}.json (MongoDB unavailable: {type(e).__name__})")


def to_records(df):
    return df.toPandas().to_dict(orient="records")
