"""Plan §8 item 4: classifier fixtures over the multi-repo layout of the split (three-repo variant).

JUDGE_REPO_DIR = the deck checkout; JUDGE_INFRA_REPOS = the agent-judge and sec-evals checkouts. The site env and
judge.env are the operator's files under a temp HOME (example values). Asserted per plan:
- site.env.example, gen-secrets.sh, rotate-secrets.md and secrets.d/*.sh are classified by LOCATION (infra in an
  infra repo, sensitive elsewhere): their names look secret-ish but they hold no values (config.py:323-327);
- *.env is ALWAYS secret, wherever it is: judge.env, site.env, the real counterpart of evals.env.example, and the
  gitignored sec-evals/site.env symlink (as named and as resolved);
- a secret path keeps the whole request sensitive; a sibling that only shares a name prefix is never infra.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from layout import JUDGE, Layout, write
from lib import config


@pytest.fixture
def lay(tmp_path, clean_env):
    lay = Layout(tmp_path)
    assert lay.home == clean_env       # HOME (set by clean_env) holds the operator files of the layout
    return lay


def cls(path, cwd=None):
    return config.path_class(str(path), config.load_config(), cwd=str(cwd) if cwd else None)


def test_config_resolves_the_split_layout(lay):
    cfg = config.load_config()
    assert cfg["SITE_ENV"] == str(lay.site_env) and cfg["JUDGE_ENV"] == str(lay.judge_env)
    assert cfg["JUDGE_REPO_DIR"] == str(lay.deck)
    assert cfg["JUDGE_INFRA_REPOS"].split() == [str(lay.aj), str(lay.se)]
    assert config.frontier_refusal(cfg) == ""
    assert config.infra_git_dirs(cfg) == {str(lay.deck / ".git"), str(lay.se / ".git")}  # agent-judge copy: no .git


@pytest.mark.parametrize("rel", ["walter/deploy.sh", "walter/README.md", "CONVENTIONS.md", "scripts/check-sanitized.sh",
                                 "site.env.example", "scripts/gen-secrets.sh", "docs/runbooks/rotate-secrets.md",
                                 "scripts/secrets.d/walter.sh", "walter/gateway/.env.example"])
def test_deck_files_by_location(lay, rel):
    assert cls(lay.deck / rel) == "infra"
    assert cls(lay.wt / rel) == "infra"            # a linked worktree of the deck (#37)
    assert cls(lay.other / rel) == "sensitive"     # the same name in an unrelated repo


@pytest.mark.parametrize("rel", ["judge/README.md", "judge/hooks/gate.py", "judge/judge.env.example",
                                 "CONVENTIONS.md", "scripts/check-sanitized.sh", "tools/deck-tools/gen-secrets.sh",
                                 "tools/deck-tools/check-sanitized.sh", "docs/runbooks/agent-judge-pilot.md"])
def test_agent_judge_files_are_infra(lay, rel):
    assert (lay.aj / rel).is_file(), rel
    assert cls(lay.aj / rel) == "infra"
    assert cls(lay.sibling_old / rel) == "sensitive"   # <src>/agent-judge-old is not <src>/agent-judge/*


@pytest.mark.parametrize("rel", ["evals/run.py", "evals/tools/lib.sh", "evals.env.example", "CONVENTIONS.md"])
def test_sec_evals_files_are_infra(lay, rel):
    assert cls(lay.se / rel) == "infra"


def test_env_files_are_always_secret(lay):
    for p in (lay.judge_env, lay.site_env, lay.deck / "site.env", lay.se / "evals.env",
              lay.se / "site.env",                      # the gitignored symlink, as named
              lay.aj / "judge.env", lay.aj / "site.env",    # would-be files at this repo's root (absent: lexical)
              lay.deck / "walter" / "gateway" / ".env", lay.deck / "site.env.bak-1"):
        assert cls(p) == "secret", p
    assert (lay.se / "site.env").resolve() == lay.site_env.resolve()
    assert cls((lay.se / "site.env").resolve()) == "secret"  # as resolved


def test_symlink_named_like_code_to_an_env_file_is_secret(lay):
    """A link with an innocent name in an infra repo that points at the site env is still secret (resolved)."""
    link = lay.aj / "judge" / "notes.txt"
    link.symlink_to(lay.site_env)
    assert cls(link) == "secret"


def test_secrets_d_by_location(lay):
    assert cls(lay.deck / "scripts/secrets.d/walter.sh") == "infra"
    assert cls(lay.other / "scripts/secrets.d/x.sh") == "sensitive"


def test_request_classes(lay):
    cfg = config.load_config()
    deck, aj, se = str(lay.deck / "walter/deploy.sh"), str(lay.aj / "judge/README.md"), str(lay.se / "evals/run.py")
    assert config.classify([deck, aj, se], cfg) == "infra"
    d = config.classify_detail([deck, aj, str(lay.judge_env)], cfg)
    assert d["class"] == "sensitive" and d["reason"] == "secret path" and d["secret"] == [str(lay.judge_env)]
    d = config.classify_detail([deck, aj, str(lay.se / "site.env")], cfg)
    assert d["class"] == "sensitive" and d["reason"] == "secret path"
    d = config.classify_detail([deck, aj, str(lay.other / "notes.md")], cfg)    # #43: one sibling path, withheld
    assert d["class"] == "infra" and d["reason"] == "mixed" and d["sensitive"] == [str(lay.other / "notes.md")]
    assert config.classify([str(lay.other / "notes.md")], cfg) == "sensitive"
    assert config.classify([], cfg, cwd=str(lay.aj)) == "infra"            # cwd decides without paths
    assert config.classify([], cfg, cwd=str(lay.sibling_old)) == "sensitive"
    assert config.classify([], cfg, cwd=str(lay.src)) == "sensitive"       # the parent of all checkouts


def test_without_infra_repos_only_the_deck_is_infra(tmp_path, clean_env):
    """Before JUDGE_INFRA_REPOS is set (CUTOVER step 4), agent-judge and sec-evals work is judged locally."""
    lay = Layout(tmp_path, infra_repos=False)
    assert cls(lay.deck / "walter/deploy.sh") == "infra"
    assert cls(lay.aj / "judge/README.md") == "sensitive"
    assert cls(lay.se / "evals/run.py") == "sensitive"


def test_clean_env_cli_matches(lay):
    """The same answers from `config.py` in a process whose environment is PATH and HOME only (hooks, units)."""
    env = lay.env()

    def cli(*a):
        p = subprocess.run([sys.executable, "-B", str(JUDGE / "lib/config.py"), *a], env=env, capture_output=True,
                           text=True, timeout=30, cwd=str(lay.src))
        assert p.returncode == 0, p.stderr
        return p.stdout.strip()
    assert cli("--site-env-path") == str(lay.site_env)
    assert cli("--classify", str(lay.deck / "walter/deploy.sh")) == "infra"
    assert cli("--classify", str(lay.aj / "judge/hooks/gate.py")) == "infra"
    assert cli("--classify", str(lay.se / "site.env")) == "sensitive"
    assert cli("--classify", str(lay.other / "notes.md")) == "sensitive"
    d = json.loads(cli("--explain", str(lay.aj / "judge/README.md"), str(lay.judge_env)))
    assert d["secret"] == [str(lay.judge_env)] and d["class"] == "sensitive"


def test_site_identifying_keys_in_judge_env_still_classify_but_install_warns(lay):
    """Plan §3: JUDGE_INFRA_REPOS belongs in site.env. If it is put in judge.env anyway, config still honours it
    (judge.env > site.env), so the classifier does not silently change; install.sh warns about it."""
    write(lay.judge_env, f"SITE_ENV=~/.config/deck/site.env\nJUDGE_INFRA_REPOS={lay.aj}\n", 0o600)
    cfg = config.load_config()
    assert cfg["JUDGE_INFRA_REPOS"] == str(lay.aj)
    assert cls(lay.aj / "judge/README.md") == "infra" and cls(lay.se / "evals/run.py") == "sensitive"
    assert "JUDGE_INFRA_REPOS" in (JUDGE / "install.sh").read_text()
