"""agent-judge as its own repo: C10 for its own root, the vendored deck-tools, and code_version carried over.

C10 (judge/contracts/infra-repo.json): every repo in JUDGE_REPO_DIR / JUDGE_INFRA_REPOS has CONVENTIONS.md and an
executable scripts/check-sanitized.sh at its root. After the cutover this repo is one of them, and verify.py runs
its sanitizer whenever the agent edits a file here (find_repo, verify.py:325-334).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys

from layout import JUDGE, REPO

C10 = json.loads((JUDGE / "contracts" / "infra-repo.json").read_text())


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def lock_version():
    m = re.search(r"^version: (\S+)$", (REPO / "tools" / "tools.lock").read_text(), re.M)
    return m.group(1)


def test_c10_root_files_and_modes():
    assert C10["contract_version"].startswith("1.")
    for rel in C10["repo_root_files"]:
        assert (REPO / rel).is_file(), rel
    staged = {line.split("\t")[1]: line.split()[0] for line in git("ls-files", "-s").splitlines()}
    for rel in C10["executable"]:
        assert staged.get(rel) == "100755", (rel, staged.get(rel))


def test_shim_execs_the_vendored_engine():
    text = (REPO / "scripts" / "check-sanitized.sh").read_text()
    assert 'exec "$here/../tools/deck-tools/check-sanitized.sh" "$@"' in text
    assert "--repo" not in text.split("exec", 1)[1]     # the repo checked is the cwd's work tree (deck convention)
    assert (REPO / "tools" / "deck-tools" / "check-sanitized.sh").is_file()


def test_verify_finds_this_root():
    spec = importlib.util.spec_from_file_location("verify_for_c10", JUDGE / "hooks" / "verify.py")
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    for rel in ("judge/hooks/gate.py", "tools/drill.py", "docs/agent-judge.md", "judge/tests/fixtures/x.json"):
        assert v.find_repo(str(REPO / rel)) == str(REPO), rel


def test_conventions_inherit_the_vendored_release():
    text = (REPO / "CONVENTIONS.md").read_text()
    m = re.search(r"Inherits local-inference-deck `CONVENTIONS.md` at `deck-tools-v([0-9.]+)`", text)
    assert m and m.group(1) == lock_version()
    assert "local-inference-deck deck-tools-v%s " % lock_version() in (REPO / "tools" / "tools.lock").read_text()


def test_deck_tools_verify():
    p = subprocess.run([sys.executable, "-B", str(REPO / "tools/deck-tools/deck-tools"), "verify", "--repo", str(REPO)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr


def test_secret_shaped_files_are_ignored_and_examples_are_not():
    def ignored(rel):
        return subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=REPO).returncode == 0
    for rel in ("site.env", "judge.env", "evals/site.env", "x.env", ".env", ".sanitize-extra", ".sanitize-words",
                "judge/lib/__pycache__/config.cpython-312.pyc", "drill-out/report.json"):
        assert ignored(rel), rel
    for rel in ("judge/judge.env.example", "judge/tests/fixtures/contracts/aliases.json", "tools/drill.py"):
        assert not ignored(rel), rel
    tracked = git("ls-files").splitlines()
    assert not [t for t in tracked if re.search(r"(^|/)(\.env|[^/]*\.env)$", t)]


def test_judge_tree_holds_only_judge_code():
    """Repo plumbing stays outside judge/ (CONVENTIONS "code_version is the judge/ tree")."""
    tracked = git("ls-files", "judge").splitlines()
    assert not [t for t in tracked if t.startswith(("judge/.github", "judge/tools/deck-tools", "judge/scripts/"))]
