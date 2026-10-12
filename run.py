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
    # HOST/PORT are set by the Dockerfile when deployed; locally it stays on localhost:8000
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Dashboard: http://localhost:{port}")
    uvicorn.run("backend.app.main:app", host=host, port=port)
