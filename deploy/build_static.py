"""Build a static version of the dashboard in site/ (no server needed).

- copies frontend/ and results/*.json
- exports the trained Spark Random Forest to JSON so the browser can score
  transactions (frontend/forest.js re-implements Spark's prediction logic)
- checks the export reproduces Spark's stored probabilities for every scored
  test transaction before writing anything

Usage: python deploy/build_static.py
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.forest import export_forest, feature_matrix, fraud_probability  # noqa: E402
from config import RESULTS_DIR  # noqa: E402

SITE = ROOT / "site"


def validate():
    rows = json.loads((RESULTS_DIR / "flagged_transactions.json").read_text())
    x = feature_matrix(
        [1 if r["type"] == "TRANSFER" else 0 for r in rows], [r["amount"] for r in rows],
        [r["old_balance_orig"] for r in rows], [r["new_balance_orig"] for r in rows],
        [r["old_balance_dest"] for r in rows], [r["new_balance_dest"] for r in rows],
        [r["hour_of_day"] for r in rows],
    )
    worst = max(abs(fraud_probability(x) - [r["fraud_probability"] for r in rows]))
    print(f"Checked {len(rows)} test transactions against Spark: max difference {worst:.6f}")
    if worst > 1e-4:  # Spark's values were stored rounded to 4 decimals
        sys.exit("Exported forest does not match Spark's predictions")


def main():
    forest = export_forest()
    validate()

    SITE.mkdir(exist_ok=True)
    for old in SITE.iterdir():
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    shutil.copytree(ROOT / "frontend", SITE, dirs_exist_ok=True)
    (SITE / "data").mkdir()
    for f in RESULTS_DIR.glob("*.json"):
        shutil.copy(f, SITE / "data" / f.name)
    (SITE / "data" / "forest.json").write_text(json.dumps(forest, separators=(",", ":")))
    (SITE / "config.js").write_text("window.STATIC_SITE = true;\n")

    size = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file()) / 1e6
    print(f"Built {SITE} ({size:.1f} MB, {len(forest['trees'])} trees)")


if __name__ == "__main__":
    main()
