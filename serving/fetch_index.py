"""Downloads the search index from a Hugging Face dataset repo when it is not on disk.

The hosted container has no Spark and no data lake, so it fetches the prebuilt index that
deploy/deploy_space_app.py uploaded. Settings (environment):
  SEARCH_INDEX_REPO  dataset repo id, e.g. your-name/paysim-search-index
  HF_TOKEN           read token, only needed when that repo is private

Run: python -m serving.fetch_index
"""
import os
import sys

from config import SEARCH_DB


def fetch(repo=None, token=None):
    """Returns True when the index is on disk afterwards."""
    if SEARCH_DB.exists():
        return True
    repo = repo or os.environ.get("SEARCH_INDEX_REPO")
    if not repo:
        return False
    from huggingface_hub import hf_hub_download

    SEARCH_DB.parent.mkdir(parents=True, exist_ok=True)
    # hf_hub_download writes to a temporary file and renames it, so a half-downloaded index is never used
    downloaded = hf_hub_download(
        repo_id=repo, repo_type="dataset", filename="search.duckdb",
        token=token or os.environ.get("HF_TOKEN") or None, local_dir=SEARCH_DB.parent)
    if os.path.abspath(downloaded) != os.path.abspath(SEARCH_DB):
        os.replace(downloaded, SEARCH_DB)
    return SEARCH_DB.exists()


if __name__ == "__main__":
    ok = fetch()
    print(f"search index {'ready at ' + str(SEARCH_DB) if ok else 'not available (set SEARCH_INDEX_REPO)'}")
    sys.exit(0 if ok else 1)
