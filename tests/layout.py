"""The multi-repo workstation layout of the split (plan step 2, three-repo variant), built under a temp dir.

    <src>/local-inference-deck   the deck checkout (JUDGE_REPO_DIR), a git repo, plus one linked worktree
    <src>/lid-wt                 a `git worktree` of the deck (infra through the shared .git, #37)
    <src>/agent-judge            this repo's tracked files, copied (JUDGE_INFRA_REPOS[0])
    <src>/sec-evals              a sec-evals checkout (JUDGE_INFRA_REPOS[1]) with the gitignored site.env symlink
    <src>/agent-judge-old        a sibling whose name starts with an infra repo's name: never infra
    <src>/other                  an unrelated repo next to them: never infra
    <home>/.config/deck/site.env the operator site env (example values only)
    <home>/.config/judge/judge.env  SITE_ENV= only

Example values only: every host and address is an example.com / TEST-NET name or address.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JUDGE = REPO / "judge"

# Example site values: a planted value (SITE_MARK) that the mask must hide, and the judge's keys.
SITE_MARK = "198.51.100.77"


def write(p: Path, text: str, mode: int = 0o644) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(mode)
    return p


def git(cwd: Path, *args: str) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def init_repo(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        write(root / rel, text, 0o755 if rel.endswith(".sh") else 0o644)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    return root


def tracked_files(repo: Path = REPO):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def copy_agent_judge(dst: Path) -> Path:
    """This repo's tracked files (working-tree contents, modes kept), as a split-out checkout would have them."""
    for rel in tracked_files():
        src = REPO / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / rel)
    return dst


DECK_FILES = {
    "CONVENTIONS.md": "# conventions (fixture)\n",
    "scripts/check-sanitized.sh": "#!/usr/bin/env bash\nexit 0\n",
    "scripts/gen-secrets.sh": "#!/usr/bin/env bash\nexit 0\n",
    "scripts/secrets.d/walter.sh": "#!/usr/bin/env bash\ngs_secret walter/x\n",
    "site.env.example": "SPARK_DOMAIN=example.com\nBACKEND_LAN_IP=192.168.122.10\n",
    "docs/runbooks/rotate-secrets.md": "# rotate secrets (fixture)\n",
    "walter/deploy.sh": "#!/usr/bin/env bash\necho deploy\n",
    "walter/README.md": "# walter (fixture)\n",
    "walter/gateway/.env.example": "LITELLM_MASTER_KEY=CHANGEME\n",
    ".gitignore": "site.env\n*.env\n!*.env.example\n",
}
SEC_EVALS_FILES = {
    "CONVENTIONS.md": "# conventions (fixture)\n",
    "scripts/check-sanitized.sh": "#!/usr/bin/env bash\nexit 0\n",
    "evals/run.py": "print('harness')\n",
    "evals/tools/lib.sh": "#!/usr/bin/env bash\n",
    "evals.env.example": "BACKEND_LAN_IP=192.168.122.10\n",
    ".gitignore": "site.env\nevals/data/\nevals/results/\n",
}


class Layout:
    """Attributes: src, home, deck, wt, aj, se, sibling_old, other, site_env, judge_env."""

    def __init__(self, tmp: Path, *, infra_repos: bool = True, judge_env: bool = True, site_env: bool = True):
        self.src = tmp / "src"
        self.home = tmp / "home"
        self.deck = init_repo(self.src / "local-inference-deck", DECK_FILES)
        self.wt = self.src / "lid-wt"
        git(self.deck, "worktree", "add", "-q", "-b", "wt", str(self.wt))
        self.aj = copy_agent_judge(self.src / "agent-judge")
        self.se = init_repo(self.src / "sec-evals", SEC_EVALS_FILES)
        self.sibling_old = init_repo(self.src / "agent-judge-old", {"judge/README.md": "# old copy\n"})
        self.other = init_repo(self.src / "other", {"notes.md": "# notes\n",
                                                    "scripts/secrets.d/x.sh": "#!/usr/bin/env bash\n"})
        self.site_env = self.home / ".config" / "deck" / "site.env"
        self.judge_env = self.home / ".config" / "judge" / "judge.env"
        repos = f"{self.aj} {self.se}" if infra_repos else ""
        if site_env:
            write(self.site_env,
                  "SPARK_DOMAIN=example.com\nSPARK_API_HOST=api.example.com\n"
                  f"BACKEND_LAN_IP={SITE_MARK}\nJUDGE_MODE=frontier\n"
                  f"JUDGE_REPO_DIR={self.deck}\nJUDGE_INFRA_REPOS=\"{repos}\"\n", 0o600)
            # the gitignored sec-evals/site.env -> the operator site env (plan §3, `deck install evals`)
            (self.se / "site.env").symlink_to(self.site_env)
            # a real-counterpart env file in each repo (gitignored there): always secret
            write(self.deck / "site.env", "SPARK_DOMAIN=example.com\n", 0o600)
            write(self.se / "evals.env", "BACKEND_LAN_IP=192.168.122.10\n", 0o600)
        if judge_env:
            write(self.judge_env, "SITE_ENV=~/.config/deck/site.env\n", 0o600)

    def env(self, **extra) -> dict:
        """A clean environment: PATH and HOME only (the hooks' and units' view after the cutover)."""
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home)}
        e.update(extra)
        return e
