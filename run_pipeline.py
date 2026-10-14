"""Runs the full batch pipeline: download -> ingest -> analytics -> model -> search index.

Usage: python run_pipeline.py
"""
import subprocess
import sys
import time

STEPS = [
    ("Download PaySim dataset from Kaggle", [sys.executable, "data/download_data.py"]),
    ("Ingest CSV into Parquet data lake", [sys.executable, "-m", "spark_jobs.ingest"]),
    ("Spark SQL analytics -> MongoDB", [sys.executable, "-m", "spark_jobs.analytics"]),
    ("Train and evaluate fraud model (Spark MLlib)", [sys.executable, "-m", "spark_jobs.train_model"]),
    ("Build the transaction search index (DuckDB)", [sys.executable, "-m", "serving.build_search_index"]),
]

for label, cmd in STEPS:
    print(f"\n=== {label} ===", flush=True)
    start = time.time()
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"Step failed: {label}")
    print(f"done in {time.time() - start:.0f}s")

print("\nPipeline finished. Start the dashboard with: python run.py")
