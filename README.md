# agent-judge

A reviewer for a local coding agent (Hermes). Shell hooks gate risky tool calls and queue reviews; a
deterministic collector gathers evidence; a frontier (or local) judge checks the agent's claims against that
evidence, never against the agent's own account. It runs entirely on the operator's workstation, as Hermes shell
hooks plus systemd user units. Stdlib Python only.

Split out of local-inference-deck (plan step 2). The deck repo is the stack the judge reviews work on: its
checkout is `JUDGE_REPO_DIR`, and its `site.env` holds the judge's site settings.

## Where things are

| Path | What |
|---|---|
| [judge/README.md](judge/README.md) | **Start here.** Installing and operating the judge: hooks, units, gate rules, configuration, data boundary, troubleshooting |
| [judge/CONTRACT.md](judge/CONTRACT.md) | The binding interfaces between the judge's parts |
| [judge/runner/units/README.md](judge/runner/units/README.md) | The systemd user units |
| [judge/judge.env.example](judge/judge.env.example) | Template of `~/.config/judge/judge.env`: `SITE_ENV=` and non-identifying policy knobs only |
| [docs/agent-judge.md](docs/agent-judge.md) | Design: why and how a judge reviews the local agent; rubric, data boundary, pilot results |
| [docs/decisions.md](docs/decisions.md) | Decisions #10 (claims-only frontier review) and #11 (mixed bundles with withheld paths) |
| [CONVENTIONS.md](CONVENTIONS.md) | Repo rules (inherits the deck's), and why repo plumbing stays outside `judge/` |

### Runbooks

- [docs/runbooks/agent-judge-pilot.md](docs/runbooks/agent-judge-pilot.md): running a judge pilot.
- [docs/runbooks/agent-judge-rejudge.md](docs/runbooks/agent-judge-rejudge.md): re-judging stored bundles.
- After an install or reinstall: `tools/drill.py config-check`, then `tools/drill.py run` (below).

Runbooks that span the stack (deploys, upgrades, secret rotation) stay in local-inference-deck `docs/runbooks/`.
Links in `judge/README.md` to `../site.env.example` mean the deck's `site.env.example` (section `# --- judge`).

## Install

1. Clone to a stable path. `JUDGE_INFRA_REPOS` and the Hermes hook commands name it, so it must not move:
   `git clone <this repo> ~/src/deck/agent-judge`.
2. In the operator's site.env (the deck's; never in judge.env): `JUDGE_REPO_DIR` = the deck checkout and
   `JUDGE_INFRA_REPOS` = this checkout plus the sec-evals checkout, absolute, space-separated.
3. `judge/install.sh --apply --with-units --start` from this checkout. It merges the `managed_by: agent-judge`
   hooks block into the Hermes config in place, merges `SITE_ENV=` into judge.env and installs the units.
4. Approve the hooks in an interactive `hermes chat` (Hermes asks once per event and command; never set
   `hooks_auto_accept`). Then `hermes hooks list` and `hermes hooks doctor`.

`judge/README.md` has the details and the options. Moving the checkout later means repeating steps 2-4.

## code_version

Every request, bundle and finding carries `code_version`: the git tree id of `judge/` (`judge/lib/version.py`).
`judge/` kept its monorepo prefix, so the split did not change it: the first commit of this repo has the same
`judge/` tree as local-inference-deck at the split point. Changes under `judge/` (code, docs or tests) change
it; repo plumbing (`tools/`, `scripts/`, `tests/`, `.github/`, `docs/`) does not.

## Tests

```sh
python3 -m pytest judge/tests -q -p no:cacheprovider     # the judge's suite
python3 -m pytest tests -q -p no:cacheprovider           # the split layout, relocated checkouts, the drill
```

Offline: temp dirs, a stub frontier CLI and a loopback stub gateway. Set `HERMES_VENV_PYTHON` to a python3 with
PyYAML to run the install tests without Hermes. CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs
both suites, `deck-tools verify`, gitleaks and the regex-only sanitizer.

## The drill

`tools/drill.py` checks an installed judge end to end without touching real review data:

- `tools/drill.py config-check` prints booleans and counts from the judge's config as the hooks see it (PATH
  and HOME only): site env resolved, frontier allowed, the class of a deck file and of an agent-judge file.
- `tools/drill.py run --work <throwaway deck worktree> --out <new temp dir>` runs three sessions (infra, mixed,
  sensitive) through the real hooks and runner, with HERMES_HOME and the review dir under `--out`, and reports
  what was sent to the frontier judge as counts. **It makes real model calls.** It prints no site value.

## Shared tooling

`tools/deck-tools/` is the vendored deck-tools release named in `tools/tools.lock` (from local-inference-deck
tags `deck-tools-v*`). `scripts/check-sanitized.sh` is the shim the judge's own verify hook runs here (contract
C10). Upgrade: `python3 <deck>/tools/deck-tools/deck-tools sync --from <deck checkout at the new tag> --repo .`
