# Repo conventions

**Inherits local-inference-deck `CONVENTIONS.md` at `deck-tools-v1.0.0`** (the deck-tools release vendored in
`tools/tools.lock`). Its rules apply here unchanged: secrets and forbidden content, "before every commit",
branches, worktrees and PRs, and the deck-tools vendoring rules. This file adds only what is specific to
agent-judge. Where the two differ, this file wins for this repo.

This repo is **PUBLIC**. Everything committed must be generic and secret-free.

## Layout
```
judge/            the agent judge, kept at its monorepo prefix (README.md, CONTRACT.md)
  hooks/ runner/  Hermes shell hooks; the review runner and its systemd user units
  collector/ lib/ evidence collector; config, data-boundary rules, site mask, queue, code version
  contracts/      contracts this repo owns: frontier-env.json (C9), infra-repo.json (C10)
  tests/          the judge's own tests; tests/fixtures/contracts/ holds vendored deck contracts (C3, C7, C8)
  judge.env.example  the template of ~/.config/judge/judge.env (SITE_ENV= and policy knobs only)
  install.sh      wires the hooks into a Hermes profile; --with-units installs the units
docs/             agent-judge.md (design), decisions.md (#10, #11), runbooks/agent-judge-*.md
tests/            repo-level tests: the split layout, relocated checkouts, the drill (outside judge/, see below)
tools/            deck-tools/ (vendored engines) and tools.lock (their sha256 and mode); drill.py
scripts/          check-sanitized.sh (executable shim into tools/deck-tools/, C10)
CONVENTIONS.md    this file (C10 needs it at the root)
```

## code_version is the judge/ tree
`judge/lib/version.py` stamps every request, bundle and finding with the git tree id of `judge/`. **Any change
under `judge/` changes it**, docs and tests included, and the runner notes a mismatch when a request was written
by other judge code. So:
- Repo plumbing (CI, tooling, repo-level tests, the drill, docs outside `judge/`) lives outside `judge/`.
- A change under `judge/` is a code change: its PR says so and says why.
- `judge/` stays at its monorepo prefix (no rename), so the tree id carried over from local-inference-deck.

## Site values
- The judge reads the operator's full site env (it is exempt from slicing: `lib/sitemask.py` can only mask the
  values it knows). The keys and their example defaults are owned by local-inference-deck
  (`contracts/site-keys.yaml`, `site.env.example`, section `# --- judge`).
- `~/.config/judge/judge.env` holds `SITE_ENV=` and non-identifying policy knobs only
  ([judge/judge.env.example](judge/judge.env.example)). `JUDGE_REPO_DIR`, `JUDGE_INFRA_REPOS` and
  `JUDGE_SSH_ALIASES` identify the site and stay in site.env, where the sanitizer checks them.
- This repo has no root `*.env.example`, so `scripts/check-sanitized.sh` runs without an example baseline: every
  site value counts as real (stricter, never looser; the engine says so in a note).

## Before every commit
As in the deck: `scripts/check-sanitized.sh --all; echo "exit=$?"` (0 clean, 1 finding, 2 no real-value source),
gitleaks on the changed paths (it reads the root `.gitleaks.toml`: the default rules plus this
repo's allowlist of four files that hold secret-shaped fakes on purpose), and a grep of the added lines for real domains, IPs, user names and home paths.
Install the hook once per clone with `tools/deck-tools/deck-tools install-hooks`.

Tests: `python3 -m pytest judge/tests -q -p no:cacheprovider` and `python3 -m pytest tests -q -p no:cacheprovider`
(offline, temp dirs only, single-threaded). The Hermes-dependent tests skip without a Hermes venv; point
`HERMES_VENV_PYTHON` at any python3 with PyYAML to run them.

## Contracts
- Owned here: `judge/contracts/frontier-env.json` (C9) and `judge/contracts/infra-repo.json` (C10). Bump
  `contract_version` as in the deck's `contracts/README.md`; consumers keep vendored copies (the deck under
  `contracts/vendored/agent-judge/`, sec-evals under `evals/tests/fixtures/contracts/`).
- Consumed here: the deck's C3 aliases, C7 observability and C8 Hermes profile, vendored byte-identical under
  `judge/tests/fixtures/contracts/`. Refresh them from a deck tag (that is a `judge/` change: code_version moves).
- This repo is itself a C10 infra repo: `CONVENTIONS.md` and an executable `scripts/check-sanitized.sh` at the
  root (`tests/test_split_repo.py`).
