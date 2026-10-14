"""Writes results/sample_upload.csv: a small labelled sample for the "Try the sample file" button.

Built with the synthetic generator (scripts/generate_dataset.py): 500 rows, about 40 of them fraud, so the
demo shows flagged rows straight away. Run: python -m scripts.make_sample_upload
"""
import numpy as np

from config import RESULTS_DIR
from scripts.generate_dataset import generate, write

OUT = RESULTS_DIR / "sample_upload.csv"
ROWS, FRAUD_ROWS = 500, 40

if __name__ == "__main__":
    cols = generate(120_000, seed=2024, labels=True)
    rng = np.random.default_rng(1)
    fraud = np.flatnonzero(cols["fraud"] == 1)
    legit = np.flatnonzero(cols["fraud"] == 0)
    pick = np.sort(np.concatenate([rng.choice(fraud, FRAUD_ROWS, replace=False), rng.choice(legit, ROWS - FRAUD_ROWS, replace=False)]))
    write({k: v[pick] for k, v in cols.items()}, str(OUT), labels=True)
    print(f"wrote {OUT} ({ROWS} rows, {FRAUD_ROWS} fraud)")
