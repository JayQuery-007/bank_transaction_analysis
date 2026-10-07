"""Starts the dashboard at http://localhost:8000

Results are read from MongoDB if it is running, otherwise from results/*.json
(committed to the repo), so this works straight after cloning.
To rebuild everything from the raw Kaggle data, run: python run_pipeline.py
"""
import os
import sys

import uvicorn

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

if __name__ == "__main__":
    print("Dashboard: http://localhost:8000")
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000)
