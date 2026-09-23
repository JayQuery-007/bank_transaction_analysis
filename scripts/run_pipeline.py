"""
Runs the full pipeline end to end:
  1. generate synthetic transaction data
  2. Spark SQL ETL + feature engineering  (falls back to pandas if no Spark/Java)
  3. train Isolation Forest anomaly detector + score every transaction
  4. load results into MongoDB               (falls back to file-serving if no Mongo)

Usage:  python scripts/run_pipeline.py
"""
import subprocess
import sys
import time

STEPS = [
    ("Generating synthetic transactions", [sys.executable, "data/generate_data.py"]),
    ("Running Spark SQL ETL + feature engineering", [sys.executable, "spark_jobs/etl_features.py"]),
    ("Training Isolation Forest + scoring transactions", [sys.executable, "ml/train_and_detect.py"]),
    ("Loading results into MongoDB", [sys.executable, "scripts/load_mongo.py"]),
]


def run():
    for label, cmd in STEPS:
        print(f"\n{'='*70}\n{label}\n{'='*70}")
        start = time.time()
        result = subprocess.run(cmd)
        elapsed = time.time() - start
        if result.returncode != 0:
            print(f"[FAILED] step '{label}' exited with code {result.returncode}", file=sys.stderr)
            sys.exit(result.returncode)
        print(f"[done in {elapsed:.1f}s]")

    print("\nPipeline complete. Start the API with:\n"
          "  uvicorn backend.app.main:app --reload --port 8000\n"
          "then open http://localhost:8000")


if __name__ == "__main__":
    run()
