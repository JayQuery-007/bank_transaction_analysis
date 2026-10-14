"""Writes results/sample_upload.csv: a small, real, labelled sample in the dataset's own CSV format.

Rows are picked deterministically from the search index (ordered by a hash of tx_id): 30 frauds, 400 legitimate TRANSFER/CASH_OUT
rows and 70 rows of other types (which the model does not score). The model saw about 70% of the
dataset during training, so scores on this sample are optimistic; it exists to demo the upload.

Run: python -m scripts.make_sample_upload
"""
import duckdb

from config import RESULTS_DIR, SEARCH_DB

OUT = RESULTS_DIR / "sample_upload.csv"

con = duckdb.connect(str(SEARCH_DB), read_only=True)
con.execute(f"""
COPY (
  SELECT step, type, amount, name_orig AS nameOrig, old_balance_orig AS oldbalanceOrg,
         new_balance_orig AS newbalanceOrig, name_dest AS nameDest, old_balance_dest AS oldbalanceDest,
         new_balance_dest AS newbalanceDest, is_fraud AS isFraud
  FROM (
    (SELECT * FROM transactions WHERE is_fraud = 1 AND type IN ('TRANSFER', 'CASH_OUT') ORDER BY hash(tx_id) LIMIT 30)
    UNION ALL
    (SELECT * FROM transactions WHERE is_fraud = 0 AND type IN ('TRANSFER', 'CASH_OUT') ORDER BY hash(tx_id) LIMIT 400)
    UNION ALL
    (SELECT * FROM transactions WHERE type IN ('PAYMENT', 'CASH_IN', 'DEBIT') ORDER BY hash(tx_id) LIMIT 70)
  )
  ORDER BY step, name_orig
) TO '{OUT.as_posix()}' (HEADER, DELIMITER ',')
""")
print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
