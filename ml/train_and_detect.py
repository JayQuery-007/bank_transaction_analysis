"""
Unsupervised anomaly detection over the Spark-engineered transaction features.

Uses an Isolation Forest (scikit-learn) trained on behavioural features
(amount, z-score vs account history, night-time flag, velocity, distinct
countries/categories touched). Produces:
  - ml/model.joblib               trained model + scaler, reloadable by the API
  - data/processed/scored.csv     every transaction with anomaly_score + is_anomaly
  - data/processed/model_metrics.json  precision/recall against the injected
                                        ground-truth fraud labels (for the report)
"""
import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix
from sklearn.preprocessing import StandardScaler

ENRICHED_CSV = "data/processed/enriched.csv"
MODEL_PATH = "ml/model.joblib"
SCORED_CSV = "data/processed/scored.csv"
METRICS_PATH = "data/processed/model_metrics.json"

FEATURES = [
    "amount", "amount_zscore", "is_night", "night_ratio",
    "distinct_countries", "distinct_categories", "hour",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["seconds_since_prev_tx"] = df["seconds_since_prev_tx"].fillna(df["seconds_since_prev_tx"].median())
    df["velocity_flag"] = (df["seconds_since_prev_tx"] < 60).astype(int)
    return df


def main():
    df = pd.read_csv(ENRICHED_CSV, parse_dates=["timestamp"])
    df = build_features(df)

    feature_cols = FEATURES + ["velocity_flag"]
    X = df[feature_cols].fillna(0).values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    contamination = float(np.clip(df["is_fraud"].mean(), 0.01, 0.1))
    model = IsolationForest(
        n_estimators=300,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_scaled)

    raw_scores = model.decision_function(X_scaled)  # higher = more normal
    df["anomaly_score"] = (-raw_scores)  # higher = more anomalous, easier to reason about
    # normalize to 0-100 for a friendlier UI scale
    smin, smax = df["anomaly_score"].min(), df["anomaly_score"].max()
    df["anomaly_score"] = ((df["anomaly_score"] - smin) / (smax - smin) * 100).round(2)

    preds = model.predict(X_scaled)  # -1 = anomaly, 1 = normal
    df["is_anomaly"] = (preds == -1).astype(int)

    os.makedirs("data/processed", exist_ok=True)
    df.sort_values("anomaly_score", ascending=False).to_csv(SCORED_CSV, index=False)

    joblib.dump({"model": model, "scaler": scaler, "feature_cols": feature_cols}, MODEL_PATH)

    metrics = {
        "contamination_used": contamination,
        "precision_vs_injected_fraud": round(precision_score(df["is_fraud"], df["is_anomaly"]), 4),
        "recall_vs_injected_fraud": round(recall_score(df["is_fraud"], df["is_anomaly"]), 4),
        "f1_vs_injected_fraud": round(f1_score(df["is_fraud"], df["is_anomaly"]), 4),
        "confusion_matrix": confusion_matrix(df["is_fraud"], df["is_anomaly"]).tolist(),
        "total_flagged": int(df["is_anomaly"].sum()),
        "total_transactions": int(len(df)),
    }
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    print("Model trained. Metrics vs injected fraud labels (for evaluation only - model is unsupervised):")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
