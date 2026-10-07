import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

RAW_CSV = ROOT / "data" / "raw" / "paysim.csv"
LAKE_DIR = ROOT / "data" / "lake" / "transactions"
MODEL_DIR = ROOT / "models" / "fraud_rf"
RESULTS_DIR = ROOT / "results"

KAGGLE_DATASET = "ealaxi/paysim1"

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = "bank_analytics"
