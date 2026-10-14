import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("deploy_space_app", Path(__file__).resolve().parents[1] / "deploy" / "deploy_space_app.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)

MONGO = "mongodb+srv://analyst:s3cret-pass@cluster0.example.mongodb.net/"
READ_TOKEN = "hf_readOnlyToken123"


class FakeApi:
    def __init__(self, remote_size=None, space_error=None, stages=("RUNNING",), logged_in=True):
        self.calls, self.remote_size, self.space_error = [], remote_size, space_error
        self.stages, self.logged_in = list(stages), logged_in

    def whoami(self):
        if not self.logged_in:
            raise RuntimeError("401")
        return {"name": "tester"}

    def create_repo(self, repo_id, **kw):
        if kw.get("repo_type") == "space" and self.space_error:
            raise RuntimeError(self.space_error)
        self.calls.append(("create_repo", kw["repo_type"], repo_id, kw.get("private")))

    def dataset_info(self, repo_id, files_metadata=False):
        files = [SimpleNamespace(rfilename="search.duckdb", size=self.remote_size)] if self.remote_size else []
        return SimpleNamespace(siblings=files)

    def upload_file(self, **kw):
        self.calls.append(("upload_file", kw["repo_type"], kw["path_in_repo"]))

    def add_space_variable(self, repo_id, key, value):
        self.calls.append(("variable", key, value))

    def add_space_secret(self, repo_id, key, value):
        self.calls.append(("secret", key, value))

    def upload_folder(self, **kw):
        self.calls.append(("upload_folder", kw["repo_id"], tuple(kw["allow_patterns"])))

    def space_info(self, repo_id):
        return SimpleNamespace(host="https://tester-bank-transaction-analytics-app.hf.space")

    def get_space_runtime(self, repo_id):
        return SimpleNamespace(stage=self.stages.pop(0) if len(self.stages) > 1 else self.stages[0])


@pytest.fixture
def index_file(tmp_path, monkeypatch):
    path = tmp_path / "search.duckdb"
    path.write_bytes(b"x" * 1234)
    monkeypatch.setattr(deploy, "SEARCH_DB", path)
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MONGO_URI", raising=False)
    monkeypatch.delenv("SPACE_HF_READ_TOKEN", raising=False)


def kinds(api):
    return [c[0] for c in api.calls]


def test_dry_run_changes_nothing_and_warns_about_missing_settings(index_file, capsys):
    api = FakeApi()
    result = deploy.main(["--dry-run"], api=api)
    out = capsys.readouterr().out
    assert api.calls == [] and result["dry_run"]
    assert "MONGO_URI is not set" in out and "SPACE_HF_READ_TOKEN is not set" in out


def test_full_deploy_does_things_in_a_safe_order(index_file, monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URI", MONGO)
    monkeypatch.setenv("SPACE_HF_READ_TOKEN", READ_TOKEN)
    api = FakeApi()
    result = deploy.main(["--no-wait"], api=api)
    assert api.calls[0] == ("create_repo", "dataset", "tester/paysim-search-index", True)  # private by default
    order = kinds(api)
    # settings are in place before the app files trigger the first build
    assert order.index("variable") < order.index("upload_folder") and order.index("secret") < order.index("upload_folder")
    assert ("variable", "SEARCH_INDEX_REPO", "tester/paysim-search-index") in api.calls
    assert ("secret", "MONGO_URI", MONGO) in api.calls and ("secret", "HF_TOKEN", READ_TOKEN) in api.calls
    assert ("upload_file", "dataset", "search.duckdb") in api.calls
    assert result["url"].endswith("bank-transaction-analytics-app.hf.space")
    printed = capsys.readouterr().out
    assert "s3cret-pass" not in printed and READ_TOKEN not in printed and MONGO not in printed


def test_only_app_files_are_uploaded_to_the_space(index_file, monkeypatch):
    monkeypatch.setenv("MONGO_URI", MONGO)
    api = FakeApi()
    deploy.main(["--no-wait"], api=api)
    patterns = next(c for c in api.calls if c[0] == "upload_folder")[2]
    assert "Dockerfile" in patterns and "backend/**" in patterns and "models/**" in patterns
    for forbidden in ("data/**", "tests/**", "spark_jobs/**", ".git/**", "docs/**", "venv/**"):
        assert forbidden not in patterns


def test_index_upload_is_skipped_when_the_same_file_is_already_there(index_file, monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URI", MONGO)
    api = FakeApi(remote_size=1234)
    deploy.main(["--no-wait"], api=api)
    assert ("upload_file", "dataset", "search.duckdb") not in api.calls
    assert "skipping" in capsys.readouterr().out
    changed = FakeApi(remote_size=999)
    deploy.main(["--no-wait"], api=changed)
    assert ("upload_file", "dataset", "search.duckdb") in changed.calls


def test_public_index_never_stores_a_token_in_the_space(index_file, monkeypatch):
    monkeypatch.setenv("MONGO_URI", MONGO)
    monkeypatch.setenv("SPACE_HF_READ_TOKEN", READ_TOKEN)
    api = FakeApi()
    deploy.main(["--no-wait", "--public-index"], api=api)
    assert api.calls[0][3] is False
    assert not any(c[0] == "secret" and c[1] == "HF_TOKEN" for c in api.calls)


def test_private_index_without_a_token_warns_but_continues(index_file, monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URI", MONGO)
    api = FakeApi()
    deploy.main(["--no-wait"], api=api)
    assert "cannot download a private index" in capsys.readouterr().out
    assert not any(c[0] == "secret" and c[1] == "HF_TOKEN" for c in api.calls)


def test_payment_required_gives_a_clear_message(index_file, monkeypatch):
    monkeypatch.setenv("MONGO_URI", MONGO)
    api = FakeApi(space_error="402 Client Error: Payment Required")
    with pytest.raises(deploy.Stop, match="PRO"):
        deploy.main(["--no-wait"], api=api)
    assert "upload_folder" not in kinds(api)


def test_not_logged_in_stops_before_changing_anything(index_file):
    api = FakeApi(logged_in=False)
    with pytest.raises(deploy.Stop, match="hf auth login"):
        deploy.main([], api=api)
    assert api.calls == []


def test_a_malformed_connection_string_is_rejected_early(index_file, monkeypatch):
    monkeypatch.setenv("MONGO_URI", "postgres://nope")
    api = FakeApi()
    with pytest.raises(deploy.Stop, match="mongodb"):
        deploy.main([], api=api)
    assert api.calls == []


def test_missing_index_file_stops_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "SEARCH_DB", tmp_path / "missing.duckdb")
    with pytest.raises(deploy.Stop, match="build_search_index"):
        deploy.main(["--dry-run"], api=FakeApi())


def test_waits_for_the_build_to_finish(index_file, monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URI", MONGO)
    api = FakeApi(stages=("BUILDING", "BUILDING", "APP_STARTING", "RUNNING"))
    result = deploy.main([], api=api, sleep=lambda _s: None)
    out = capsys.readouterr().out
    assert result["stage"] == "RUNNING" and "stage: BUILDING" in out and "Live: https://tester-" in out


def test_a_failed_build_points_to_the_logs(index_file, monkeypatch):
    monkeypatch.setenv("MONGO_URI", MONGO)
    api = FakeApi(stages=("BUILDING", "BUILD_ERROR"))
    with pytest.raises(deploy.Stop, match="BUILD_ERROR"):
        deploy.main([], api=api, sleep=lambda _s: None)
