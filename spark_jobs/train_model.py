"""Step 3 - Fraud detection model with Spark MLlib.

Analytics showed fraud only occurs in TRANSFER and CASH_OUT, so the model is
trained on those 2.77M rows. Two models are compared on a held-out 30% test set:

- Logistic Regression (scaled features, class-weighted for the 1:337 imbalance)
- Random Forest

Both are also compared with the rule the dataset already contains
(isFlaggedFraud), which acts as the "existing system" baseline.

Run: python -m spark_jobs.train_model
"""
import time

from pyspark.ml import Pipeline
from pyspark.ml.classification import LogisticRegression, RandomForestClassifier
from pyspark.ml.evaluation import BinaryClassificationEvaluator
from pyspark.ml.feature import StandardScaler, VectorAssembler
from pyspark.ml.functions import vector_to_array
from pyspark.sql import functions as F

from config import LAKE_DIR, MODEL_DIR
from spark_jobs.common import get_spark, publish, to_records

FEATURES = [
    "is_transfer",
    "amount",
    "old_balance_orig",
    "new_balance_orig",
    "old_balance_dest",
    "new_balance_dest",
    "error_balance_orig",
    "error_balance_dest",
    "hour_of_day",
]


def shorten_part_files(model_dir):
    """Rename Spark's part-00000-<uuid>-c000.snappy.parquet files to part-00000.parquet.

    The long names push the model path past Windows' 260-character limit when the
    repo is cloned into a deep folder. Spark reads every part file in a directory
    regardless of name.
    """
    for crc in model_dir.rglob("*.crc"):
        crc.unlink()
    for folder in {p.parent for p in model_dir.rglob("part-*.parquet")}:
        for i, part in enumerate(sorted(folder.glob("part-*.parquet"))):
            part.rename(folder / f"part-{i:05d}.parquet")


def load_training_data(spark):
    df = (
        spark.read.parquet(str(LAKE_DIR))
        .filter(F.col("type").isin("TRANSFER", "CASH_OUT"))
        .withColumn("is_transfer", (F.col("type") == "TRANSFER").cast("int"))
        .withColumn("label", F.col("is_fraud").cast("double"))
    )
    n_fraud = df.filter("is_fraud = 1").count()
    n_legit = df.filter("is_fraud = 0").count()
    # balanced class weights so the minority class isn't ignored
    df = df.withColumn(
        "weight",
        F.when(F.col("is_fraud") == 1, n_legit / n_fraud).otherwise(1.0),
    )
    return df, n_fraud, n_legit


def evaluate(name, predictions, train_seconds=None):
    counts = {
        (r["is_fraud"], int(r["prediction"])): r["count"]
        for r in predictions.groupBy("is_fraud", "prediction").count().collect()
    }
    tp, fp = counts.get((1, 1), 0), counts.get((0, 1), 0)
    fn, tn = counts.get((1, 0), 0), counts.get((0, 0), 0)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    row = {
        "model": name,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "train_seconds": train_seconds,
    }
    if "rawPrediction" in predictions.columns:
        for metric in ("areaUnderROC", "areaUnderPR"):
            ev = BinaryClassificationEvaluator(labelCol="label", rawPredictionCol="rawPrediction", metricName=metric)
            row[metric] = round(ev.evaluate(predictions), 4)
    print(row)
    return row


def main():
    spark = get_spark("paysim-fraud-model")
    df, n_fraud, n_legit = load_training_data(spark)
    print(f"Training data: {n_fraud + n_legit:,} rows ({n_fraud:,} fraud, {n_legit:,} legit)")

    train, test = df.randomSplit([0.7, 0.3], seed=42)
    train.cache()
    test.cache()

    assembler = VectorAssembler(inputCols=FEATURES, outputCol="features")

    lr = Pipeline(stages=[
        assembler,
        StandardScaler(inputCol="features", outputCol="scaled", withMean=True),
        LogisticRegression(featuresCol="scaled", labelCol="label", weightCol="weight", maxIter=100),
    ])
    rf = Pipeline(stages=[
        assembler,
        RandomForestClassifier(labelCol="label", numTrees=60, maxDepth=12, seed=42),
    ])

    results = []
    fitted = {}
    for name, pipeline in [("Logistic Regression", lr), ("Random Forest", rf)]:
        start = time.time()
        model = pipeline.fit(train)
        seconds = round(time.time() - start, 1)
        fitted[name] = model
        results.append(evaluate(name, model.transform(test), seconds))

    baseline = test.withColumn("prediction", F.col("is_flagged_fraud").cast("double"))
    results.append(evaluate("Existing rule (isFlaggedFraud)", baseline))

    publish("model_metrics", results)
    publish("model_info", {
        "training_rows": train.count(),
        "test_rows": test.count(),
        "test_fraud": test.filter("is_fraud = 1").count(),
        "fraud_weight": round(n_legit / n_fraud, 1),
        "features": FEATURES,
        "selected_model": "Random Forest",
    })

    rf_model = fitted["Random Forest"]
    importances = rf_model.stages[-1].featureImportances.toArray()
    publish("feature_importance", sorted(
        [{"feature": f, "importance": round(float(v), 4)} for f, v in zip(FEATURES, importances)],
        key=lambda r: -r["importance"],
    ))

    scored = (
        rf_model.transform(test)
        .withColumn("fraud_probability", vector_to_array("probability")[1])
        .cache()
    )

    # precision/recall trade-off for the chosen model at different alert thresholds
    curve = []
    for t in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        agg = scored.agg(
            F.sum(((F.col("fraud_probability") >= t) & (F.col("is_fraud") == 1)).cast("int")).alias("tp"),
            F.sum(((F.col("fraud_probability") >= t) & (F.col("is_fraud") == 0)).cast("int")).alias("fp"),
            F.sum(((F.col("fraud_probability") < t) & (F.col("is_fraud") == 1)).cast("int")).alias("fn"),
        ).first()
        tp, fp, fn = agg["tp"], agg["fp"], agg["fn"]
        curve.append({
            "threshold": t,
            "precision": round(tp / (tp + fp), 4) if tp + fp else 0.0,
            "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
            "alerts": tp + fp,
        })
    publish("threshold_curve", curve)

    flagged = (
        scored.filter("prediction = 1 OR is_fraud = 1")
        .withColumn(
            "outcome",
            F.when((F.col("prediction") == 1) & (F.col("is_fraud") == 1), "true positive")
            .when(F.col("prediction") == 1, "false positive")
            .otherwise("missed fraud"),
        )
        .select(
            "step", "day", "hour_of_day", "type", "amount", "name_orig", "name_dest",
            "old_balance_orig", "new_balance_orig", "old_balance_dest", "new_balance_dest",
            F.round("fraud_probability", 4).alias("fraud_probability"), "is_fraud", "outcome",
        )
        .orderBy(F.desc("fraud_probability"))
    )
    publish("flagged_transactions", to_records(flagged))

    rf_model.write().overwrite().save(str(MODEL_DIR))
    shorten_part_files(MODEL_DIR)
    print(f"Saved model to {MODEL_DIR}")
    spark.stop()


if __name__ == "__main__":
    main()
