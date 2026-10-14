"""Writes three large CSVs of real PaySim rows (each over a million rows) to data/samples/, for demos.

  paysim_sample_1_1200000_rows.csv   the first 1.2 million rows (the start of the month)
  paysim_sample_2_2000000_rows.csv   2 million rows from the middle of the month
  paysim_sample_3_3000000_rows.csv   3 million rows picked evenly across the whole month

Same columns as the dataset (including isFraud, so the upload page can report accuracy).
The model saw about 70% of these rows during training, so its scores on them are optimistic.
Not committed to Git (hundreds of MB); rebuild any time with: python -m scripts.make_big_samples
"""
import duckdb

from config import ROOT, SEARCH_DB

OUT = ROOT / "data" / "samples"

FILES = [
    ("paysim_sample_1_1200000_rows.csv", "tx_id <= 1200000"),
    ("paysim_sample_2_2000000_rows.csv", "tx_id BETWEEN 2000001 AND 4000000"),
    ("paysim_sample_3_3000000_rows.csv", "hash(tx_id) % 1000 < 472"),  # ~47.2% of 6.36M
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(SEARCH_DB), read_only=True)
    for name, condition in FILES:
        path = OUT / name
        con.execute(f"""
            COPY (SELECT step, type, amount, name_orig AS nameOrig, old_balance_orig AS oldbalanceOrg,
                         new_balance_orig AS newbalanceOrig, name_dest AS nameDest, old_balance_dest AS oldbalanceDest,
                         new_balance_dest AS newbalanceDest, is_fraud AS isFraud
                  FROM transactions WHERE {condition} ORDER BY tx_id)
            TO '{path.as_posix()}' (HEADER, DELIMITER ',')
        """)
        rows, frauds = con.execute(f"SELECT count(*), sum(is_fraud) FROM transactions WHERE {condition}").fetchone()
        print(f"{name}: {rows:,} rows, {int(frauds):,} labelled fraud, {path.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
