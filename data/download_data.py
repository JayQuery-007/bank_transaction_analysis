"""Download the PaySim dataset from Kaggle into data/raw/paysim.csv.

Uses kagglehub, which reads your Kaggle API token from ~/.kaggle/.
Without a token, download the CSV manually from
https://www.kaggle.com/datasets/ealaxi/paysim1 and save it as data/raw/paysim.csv.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import KAGGLE_DATASET, RAW_CSV  # noqa: E402


def main():
    if RAW_CSV.exists():
        print(f"{RAW_CSV} already exists ({RAW_CSV.stat().st_size / 1e6:.0f} MB), skipping download")
        return

    import kagglehub

    folder = Path(kagglehub.dataset_download(KAGGLE_DATASET))
    csv_files = list(folder.glob("*.csv"))
    if not csv_files:
        sys.exit(f"No CSV found in {folder}")

    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(csv_files[0], RAW_CSV)
    print(f"Saved {RAW_CSV} ({RAW_CSV.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
