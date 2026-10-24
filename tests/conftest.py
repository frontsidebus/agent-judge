"""Repo-level tests of agent-judge as a split-out repo (plan step 2). They live outside judge/ on purpose: the
judge's code_version is the git tree id of judge/, so tests of the repo layout do not change it.

Offline, temp dirs only, example values only. Run: python3 -m pytest tests judge/tests -q -p no:cacheprovider
"""
import os
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
JUDGE = HERE.parent / "judge"
for p in (str(HERE), str(JUDGE)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Every key that could make a test read the operator's real files or change a classification.
CLEAN = ("SITE_ENV", "JUDGE_ENV", "JUDGE_REPO_DIR", "JUDGE_INFRA_REPOS", "JUDGE_MODE", "JUDGE_SSH_ALIASES",
         "HERMES_HOME", "JUDGE_REVIEW_DIR", "JUDGE_FRONTIER_CMD", "JUDGE_LOCAL_URL", "JUDGE_SECRET_GLOBS",
         "JUDGE_SCRATCH_GLOBS", "JUDGE_MIXED_MAX_SENSITIVE", "SPARK_DOMAIN", "SPARK_API_HOST")


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """HOME is an empty temp dir and no judge/site variable is set, for in-process config calls."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for k in CLEAN:
        monkeypatch.delenv(k, raising=False)
    for k in [k for k in os.environ if k.startswith("JUDGE_")]:
        monkeypatch.delenv(k, raising=False)
    return home
