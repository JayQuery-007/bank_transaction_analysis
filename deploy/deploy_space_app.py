"""Deploy the full dashboard (search, cases, upload) to a Hugging Face Docker Space.

Before running (one-time, you do these):
  1. Hugging Face account with PRO (Docker Spaces are not free) and:  hf auth login
  2. A MongoDB Atlas free cluster and a database user; copy its connection string
  3. In THIS terminal, set the connection string so it is never typed into a chat or a file:
        PowerShell:  $env:MONGO_URI = "mongodb+srv://user:password@cluster.mongodb.net/"
  4. Only if the index repo is private (the default): create a fine-grained, read-only token for
     that repo and set  $env:SPACE_HF_READ_TOKEN = "hf_..."   (a token with write access must NOT be used)

Run:   python deploy/deploy_space_app.py [--dry-run] [--public-index]

What it does, in this order, so the first build already has its settings:
  - creates the dataset repo for the search index (private unless --public-index) and uploads
    data/search.duckdb to it (skipped when the same file is already there)
  - creates the Docker Space and sets SEARCH_INDEX_REPO (variable), MONGO_URI and HF_TOKEN (secrets)
  - uploads the app files; Hugging Face then builds the image
  - waits for the build and prints the app URL
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import SEARCH_DB  # noqa: E402

APP_FILES = ["Dockerfile", ".dockerignore", "requirements-app.txt", "config.py", "run.py",
             "backend/**", "serving/**", "frontend/**", "models/**", "results/**"]
IGNORE = ["**/__pycache__/**", "**/*.crc", "**/*.pyc"]
DATASET_README = """---
license: other
---
# PaySim search index (DuckDB)

Search index built from the PaySim dataset (https://www.kaggle.com/datasets/ealaxi/paysim1) for the
Bank Transaction Analytics project: https://github.com/JayQuery-007/bank_transaction_analysis

The data is the work of Lopez-Rojas, Elmir and Axelsson, "PaySim: a financial mobile money simulator
for fraud detection", EMSS 2016. Check the dataset's licence on Kaggle before making this repo public.
"""


class Stop(SystemExit):
    pass


def space_url_from(info, repo_id):
    host = getattr(info, "host", None)
    return host or "https://" + repo_id.replace("/", "-").replace("_", "-").replace(".", "-").lower() + ".hf.space"


def main(argv=None, api=None, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--space", default="bank-transaction-analytics-app")
    parser.add_argument("--index-repo", default="paysim-search-index")
    parser.add_argument("--public-index", action="store_true", help="make the index dataset repo public (check the PaySim licence first)")
    parser.add_argument("--skip-index-upload", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="check files and settings, change nothing")
    args = parser.parse_args(argv)

    mongo_uri = os.environ.get("MONGO_URI")
    read_token = os.environ.get("SPACE_HF_READ_TOKEN")
    private_index = not args.public_index

    problems = []
    if not args.skip_index_upload and not SEARCH_DB.exists():
        problems.append(f"{SEARCH_DB} not found. Build it with: python -m serving.build_search_index")
    for folder in ("models", "results", "frontend", "backend", "serving"):
        if not (ROOT / folder).exists():
            problems.append(f"{folder}/ not found")
    if problems:
        raise Stop("Cannot deploy:\n  - " + "\n  - ".join(problems))

    warnings = []
    if not mongo_uri:
        warnings.append("MONGO_URI is not set: Cases and Upload will answer 503 until you add it as a Space secret.")
    elif not mongo_uri.startswith(("mongodb://", "mongodb+srv://")):
        raise Stop("MONGO_URI must start with mongodb:// or mongodb+srv://")
    if private_index and not read_token:
        warnings.append("SPACE_HF_READ_TOKEN is not set: the Space cannot download a private index. "
                        "Set it, or use --public-index after checking the PaySim licence.")
    if read_token and read_token.startswith("hf_") is False:
        warnings.append("SPACE_HF_READ_TOKEN does not look like a Hugging Face token.")

    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()
    try:
        user = api.whoami()["name"]
    except Exception:
        raise Stop("Not logged in to Hugging Face. Run: hf auth login")

    index_repo = f"{user}/{args.index_repo}"
    space_repo = f"{user}/{args.space}"
    size_mb = SEARCH_DB.stat().st_size / 1e6 if SEARCH_DB.exists() else 0
    print(f"User:        {user}")
    print(f"Index repo:  {index_repo} ({'private' if private_index else 'PUBLIC'}), search.duckdb {size_mb:.0f} MB")
    print(f"Space:       {space_repo} (docker)")
    print(f"MONGO_URI:   {'will be set as a secret' if mongo_uri else 'NOT SET'}")
    for w in warnings:
        print(f"WARNING: {w}")
    if args.dry_run:
        print("Dry run: nothing was changed.")
        return {"space": space_repo, "index_repo": index_repo, "dry_run": True}

    # 1. the search index
    if not args.skip_index_upload:
        api.create_repo(index_repo, repo_type="dataset", private=private_index, exist_ok=True)
        remote = {f.rfilename: f.size for f in (api.dataset_info(index_repo, files_metadata=True).siblings or [])}
        if remote.get("search.duckdb") == SEARCH_DB.stat().st_size:
            print("Index already uploaded (same size), skipping.")
        else:
            print(f"Uploading the index ({size_mb:.0f} MB), this takes a while...")
            api.upload_file(path_or_fileobj=str(SEARCH_DB), path_in_repo="search.duckdb", repo_id=index_repo, repo_type="dataset")
        api.upload_file(path_or_fileobj=DATASET_README.encode(), path_in_repo="README.md", repo_id=index_repo, repo_type="dataset")

    # 2. the Space and its settings, before the first build
    try:
        api.create_repo(space_repo, repo_type="space", space_sdk="docker", exist_ok=True)
    except Exception as err:
        if "402" in str(err) or "Payment Required" in str(err):
            raise Stop("Hugging Face refused to create a Docker Space (402 Payment Required).\n"
                       "Docker Spaces need a PRO subscription: https://huggingface.co/pro")
        raise
    api.add_space_variable(space_repo, "SEARCH_INDEX_REPO", index_repo)
    if mongo_uri:
        api.add_space_secret(space_repo, "MONGO_URI", mongo_uri)
    if private_index and read_token:
        api.add_space_secret(space_repo, "HF_TOKEN", read_token)

    # 3. the app
    api.upload_folder(repo_id=space_repo, repo_type="space", folder_path=str(ROOT), allow_patterns=APP_FILES,
                      ignore_patterns=IGNORE, delete_patterns=["backend/**", "serving/**", "frontend/**", "models/**", "results/**"],
                      commit_message="Deploy dashboard")
    api.upload_file(path_or_fileobj=str(ROOT / "deploy" / "space_README_docker.md"), path_in_repo="README.md",
                    repo_id=space_repo, repo_type="space", commit_message="Space README")

    url = space_url_from(api.space_info(space_repo), space_repo)
    print(f"Space page: https://huggingface.co/spaces/{space_repo}")
    print(f"App URL:    {url}")
    if args.no_wait:
        return {"space": space_repo, "url": url}

    print("Waiting for the build (the first one takes several minutes)...")
    deadline = time.time() + 25 * 60
    stage = None
    while time.time() < deadline:
        current = api.get_space_runtime(space_repo).stage
        if current != stage:
            print(f"  stage: {current}")
            stage = current
        if current == "RUNNING":
            print(f"Live: {url}   (health: {url}/api/health)")
            return {"space": space_repo, "url": url, "stage": current}
        if current in ("BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"):
            raise Stop(f"The Space stopped with {current}. Open the Logs tab: https://huggingface.co/spaces/{space_repo}?logs=build")
        sleep(10)
    raise Stop("Timed out waiting for the build. Check the Space's Logs tab.")


if __name__ == "__main__":
    try:
        main()
    except Stop as stop:
        sys.exit(str(stop))
