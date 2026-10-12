"""Deploy the dashboard to a Hugging Face Space (Docker).

One-time setup: create a Hugging Face account and a write token at
https://huggingface.co/settings/tokens, then log in with:  hf auth login

Usage: python deploy/deploy_hf.py [space-name]     (default: bank-transaction-analytics)

Creates the Space if needed and uploads only what the container needs. Hugging Face
then builds the Dockerfile and serves the app at https://<user>-<space-name>.hf.space
"""
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "Dockerfile",
    ".dockerignore",
    "requirements-app.txt",
    "config.py",
    "run.py",
    "backend/**",
    "frontend/**",
    "models/**",
    "results/**",
    "spark_jobs/__init__.py",
    "spark_jobs/common.py",
]


def main():
    space_name = sys.argv[1] if len(sys.argv) > 1 else "bank-transaction-analytics"
    api = HfApi()
    user = api.whoami()["name"]
    repo_id = f"{user}/{space_name}"

    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="space",
        folder_path=ROOT,
        allow_patterns=FILES,
        ignore_patterns=["**/__pycache__/**", "**/*.crc"],
        delete_patterns=["backend/**", "frontend/**", "models/**", "results/**"],
        commit_message="Deploy dashboard",
    )
    api.upload_file(
        repo_id=repo_id,
        repo_type="space",
        path_or_fileobj=ROOT / "deploy" / "space_README.md",
        path_in_repo="README.md",
        commit_message="Space README",
    )

    subdomain = repo_id.replace("/", "-").replace("_", "-").lower()
    print(f"Space:   https://huggingface.co/spaces/{repo_id}")
    print(f"App URL: https://{subdomain}.hf.space  (first build takes a few minutes)")


if __name__ == "__main__":
    main()
