"""Scores a single transaction with the saved Spark MLlib Random Forest.

Spark is started on the first request (takes a few seconds) and reused after that.
"""
import threading

import pandas as pd

from config import MODEL_DIR

_lock = threading.Lock()
_state = {}


def _load():
    with _lock:
        if "model" not in _state:
            from pyspark.ml import PipelineModel

            from spark_jobs.common import get_spark

            spark = get_spark("fraud-api")
            # load through the JVM: PipelineModel.load() in Python starts Python
            # workers, which crash on Windows with Python 3.12+
            jmodel = spark._jvm.org.apache.spark.ml.PipelineModel.load(str(MODEL_DIR))
            _state["spark"] = spark
            _state["model"] = PipelineModel._from_java(jmodel)
    return _state["spark"], _state["model"]


def score(tx):
    from pyspark.ml.functions import vector_to_array

    spark, model = _load()
    row = {
        "is_transfer": 1 if tx["type"] == "TRANSFER" else 0,
        "amount": tx["amount"],
        "old_balance_orig": tx["old_balance_orig"],
        "new_balance_orig": tx["new_balance_orig"],
        "old_balance_dest": tx["old_balance_dest"],
        "new_balance_dest": tx["new_balance_dest"],
        "error_balance_orig": tx["new_balance_orig"] + tx["amount"] - tx["old_balance_orig"],
        "error_balance_dest": tx["old_balance_dest"] + tx["amount"] - tx["new_balance_dest"],
        "hour_of_day": tx["hour_of_day"],
    }
    df = spark.createDataFrame(pd.DataFrame([row]))
    result = (
        model.transform(df)
        .select(vector_to_array("probability")[1].alias("p"), "prediction")
        .first()
    )
    return {
        "fraud_probability": round(float(result["p"]), 4),
        "is_fraud": bool(result["prediction"] == 1.0),
        "error_balance_orig": round(row["error_balance_orig"], 2),
        "error_balance_dest": round(row["error_balance_dest"], 2),
    }
