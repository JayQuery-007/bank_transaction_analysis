"""Deploy the dashboard to a free static Hugging Face Space.

One-time setup: create a Hugging Face account and log in with:  hf auth login

Usage: python deploy/deploy_hf.py [space-name]     (default: bank-transaction-analytics)

Builds site/ (see build_static.py), creates the Space if needed and uploads it.
Docker Spaces need a paid plan on Hugging Face; for a server-based deployment use the
Dockerfile on any container host instead.
"""
import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))
import build_static  # noqa: E402


def main():
    space_name = sys.argv[1] if len(sys.argv) > 1 else "bank-transaction-analytics"
    build_static.main()

    api = HfApi()
    repo_id = f"{api.whoami()['name']}/{space_name}"
    api.create_repo(repo_id, repo_type="space", space_sdk="static", exist_ok=True)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="space",
        folder_path=build_static.SITE,
        # remove files no longer in the build (keeps the Space's README and .gitattributes)
        delete_patterns=["*.html", "*.js", "*.css", "data/**", "vendor/**"],
        commit_message="Deploy dashboard",
    )
    api.upload_file(
        repo_id=repo_id,
        repo_type="space",
        path_or_fileobj=ROOT / "deploy" / "space_README.md",
        path_in_repo="README.md",
        commit_message="Space README",
    )

    subdomain = repo_id.replace("/", "-").replace("_", "-").replace(".", "-").lower()
    print(f"Space page: https://huggingface.co/spaces/{repo_id}")
    print(f"App URL:    https://{subdomain}.static.hf.space")


if __name__ == "__main__":
    main()
