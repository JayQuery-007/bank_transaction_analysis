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

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import MODEL_DIR, RESULTS_DIR  # noqa: E402

SITE = ROOT / "site"
FEATURES = [
    "is_transfer", "amount", "old_balance_orig", "new_balance_orig", "old_balance_dest",
    "new_balance_dest", "error_balance_orig", "error_balance_dest", "hour_of_day",
]


def export_forest():
    """Spark RF data -> per-tree arrays indexed by node id."""
    stage = next(MODEL_DIR.glob("stages/*RandomForestClassifier*"))
    rows = pq.read_table(next((stage / "data").glob("*.parquet"))).to_pylist()

    trees = {}
    for row in rows:
        trees.setdefault(row["treeID"], []).append(row["nodeData"])

    out = []
    for tree_id in sorted(trees):
        nodes = sorted(trees[tree_id], key=lambda n: n["id"])
        assert [n["id"] for n in nodes] == list(range(len(nodes)))
        f, t, left, right, p = [], [], [], [], []
        for n in nodes:
            leaf = n["leftChild"] == -1
            split = n["split"]
            if not leaf:
                assert split["numCategories"] == -1  # all features are continuous
            stats = n["impurityStats"]
            f.append(-1 if leaf else split["featureIndex"])
            t.append(0 if leaf else split["leftCategoriesOrThreshold"][0])
            left.append(n["leftChild"])
            right.append(n["rightChild"])
            p.append(stats[1] / sum(stats) if leaf else 0)
        out.append({"f": f, "t": t, "l": left, "r": right, "p": p})
    return {"features": FEATURES, "trees": out}


def features_of(tx):
    return [
        1 if tx["type"] == "TRANSFER" else 0,
        tx["amount"],
        tx["old_balance_orig"],
        tx["new_balance_orig"],
        tx["old_balance_dest"],
        tx["new_balance_dest"],
        tx["new_balance_orig"] + tx["amount"] - tx["old_balance_orig"],
        tx["old_balance_dest"] + tx["amount"] - tx["new_balance_dest"],
        tx["hour_of_day"],
    ]


def fraud_probability(forest, x):
    # same as Spark: each tree contributes its leaf's class distribution; average them
    total = 0.0
    for tree in forest["trees"]:
        i = 0
        while tree["f"][i] != -1:
            i = tree["l"][i] if x[tree["f"][i]] <= tree["t"][i] else tree["r"][i]
        total += tree["p"][i]
    return total / len(forest["trees"])


def validate(forest):
    rows = json.loads((RESULTS_DIR / "flagged_transactions.json").read_text())
    worst = max(abs(fraud_probability(forest, features_of(r)) - r["fraud_probability"]) for r in rows)
    print(f"Checked {len(rows)} test transactions against Spark: max difference {worst:.6f}")
    if worst > 1e-4:  # Spark's values were stored rounded to 4 decimals
        sys.exit("Exported forest does not match Spark's predictions")


def main():
    forest = export_forest()
    validate(forest)

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
