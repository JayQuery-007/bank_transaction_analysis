"""
One-command launcher (any OS):  python run.py

Builds the dataset/model on first run (Spark ETL -> Isolation Forest -> MongoDB),
then starts the dashboard at http://localhost:8000.
Spark falls back to pandas if Java is missing; the API falls back to local files
if MongoDB isn't running.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

if not (os.path.exists("data/processed/scored.csv") and os.path.exists("ml/model.joblib")):
    print("First run: building data + model (takes a minute)...")
    code = subprocess.run([sys.executable, "scripts/run_pipeline.py"]).returncode
    if code != 0:
        sys.exit(code)

import uvicorn  # noqa: E402

print("\nDashboard: http://localhost:8000\n")
uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000)
