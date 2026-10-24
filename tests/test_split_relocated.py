"""Plan §8 item 3: the split-out checkout, relocated, with an EMPTY environment and no judge.env.

The checkout under test is a git copy of this repo at <src>/agent-judge, next to a deck checkout and a sec-evals
checkout. Everything a location-based fallback could pick up is planted next to it: a site.env at this repo's
root and at the deck root (with JUDGE_REPO_DIR / JUDGE_INFRA_REPOS naming all three checkouts), and the
sec-evals/site.env symlink. HOME is empty (no ~/.config/judge/judge.env, no ~/.config/deck/site.env), the
environment holds only PATH and HOME, and `claude` and `ssh` on PATH are recording stubs.

Each hook (gate, enqueue, verify, inject), the runner (run_judge.py <id> and --pending, as the unit runs it),
rejudge.py and the runaway watcher must: make no frontier call (and no ssh), classify no sibling dir as infra,
and leave a clear message naming what to configure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from layout import SITE_MARK, Layout, git, write

RECORDER = """#!{py}
import json, os, sys
rec = {rec!r}
n = len(os.listdir(rec)) + 1
open(os.path.join(rec, "{name}-%d.json" % n), "w").write(json.dumps({{"argv": sys.argv[1:], "stdin": sys.stdin.read()
    if not sys.stdin.isatty() else ""}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": '{{"items": []}}'}}))
"""
SESSION = "20261024_031000_a1b2c3"


@pytest.fixture
def rel(tmp_path):
    lay = Layout(tmp_path, judge_env=False, site_env=False)
    git(lay.aj, "init", "-q", "-b", "main")            # a real split checkout is a git clone
    git(lay.aj, "add", "-A")
    git(lay.aj, "commit", "-q", "-m", "copy")
    planted = (f"SPARK_DOMAIN=example.com\nSPARK_API_HOST=api.example.com\nBACKEND_LAN_IP={SITE_MARK}\n"
               f"JUDGE_MODE=frontier\nJUDGE_REPO_DIR={lay.deck}\nJUDGE_INFRA_REPOS=\"{lay.aj} {lay.se}\"\n")
    write(lay.aj / "site.env", planted, 0o600)          # what the removed REPO_ROOT fallback would have read
    write(lay.deck / "site.env", planted, 0o600)
    (lay.se / "site.env").symlink_to(lay.deck / "site.env")
    (lay.home / ".hermes").mkdir(parents=True)
    (lay.home / ".hermes" / "config.yaml").write_text("model: example\n")
    lay.rec = tmp_path / "calls"
    lay.rec.mkdir()
    stub = tmp_path / "bin"
    stub.mkdir()
    for name in ("claude", "ssh"):
        (stub / name).write_text(RECORDER.format(py=sys.executable, rec=str(lay.rec), name=name))
        (stub / name).chmod(0o755)
    lay.run_env = {"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(lay.home)}
    lay.review = lay.home / ".hermes" / "review"

    def run(relpath, *args, stdin="", cwd=None, timeout=90):
        return subprocess.run([sys.executable, str(lay.aj / "judge" / relpath), *args], input=stdin,
                              capture_output=True, text=True, env=lay.run_env, cwd=str(cwd or lay.aj),
                              timeout=timeout)
    lay.run = run
    lay.calls = lambda: sorted(p.name for p in lay.rec.iterdir())
    return lay


def payload(event, cwd, **kw):
    p = {"hook_event_name": event, "tool_name": None, "tool_input": None, "session_id": SESSION, "cwd": str(cwd),
         "profile": "default", "extra": {}}
    p.update(kw)
    return json.dumps(p)


def requests(review: Path):
    out = []
    for sub in ("queue", "queue/deferred", "done"):
        d = review / sub
        if d.is_dir():
            out += [json.loads(p.read_text()) for p in d.glob("*.json")]
    return out


def edited(rel):
    return [rel.deck / "walter/deploy.sh", rel.aj / "judge/README.md", rel.se / "evals/run.py",
            rel.wt / "walter/README.md"]


def test_config_resolves_nothing_and_no_sibling_is_infra(rel):
    assert rel.run("lib/config.py", "--site-env-path").stdout.strip() == ""
    assert rel.run("lib/config.py", "--get", "JUDGE_REPO_DIR").stdout.strip() == ""
    assert rel.run("lib/config.py", "--get", "JUDGE_INFRA_REPOS").stdout.strip() == ""
    for p in edited(rel) + [rel.aj, rel.deck, rel.se, rel.src]:
        assert rel.run("lib/config.py", "--classify", str(p)).stdout.strip() == "sensitive", p
    for cwd in (rel.aj, rel.deck, rel.se, rel.src):
        d = json.loads(rel.run("lib/config.py", "--explain", cwd=cwd).stdout)
        assert d["class"] == "sensitive" and d["reason"] == "no paths: cwd is not infra", cwd


def test_hooks_send_nothing_classify_nothing_infra_and_say_why(rel):
    p = rel.run("hooks/enqueue.py", stdin=payload("on_session_start", rel.aj))
    assert p.returncode == 0 and p.stdout.strip() == "{}"
    log = (rel.review / "hook-errors.log").read_text()
    assert "frontier judge refused for this session" in log and "site env unresolved" in log
    assert "~/.config/deck/site.env" in log and "judge.env" in log      # names what to configure
    for i, f in enumerate(edited(rel)):
        write(f, f.read_text() + "edit\n" if f.exists() else "edit\n")
        for event in ("pre_tool_call",):
            g = rel.run("hooks/gate.py", stdin=payload(event, rel.aj, tool_name="write_file",
                                                        tool_input={"path": str(f), "content": "edit\n"}))
            assert g.returncode in (0, 2), g.stderr
            json.loads(g.stdout or "{}")
        p = rel.run("hooks/enqueue.py", stdin=payload("post_tool_call", rel.aj, tool_name="write_file",
                                                       tool_input={"path": str(f), "content": "edit\n"},
                                                       extra={"status": "ok", "tool_call_id": f"c{i}"}))
        assert p.returncode == 0 and p.stdout.strip() == "{}"
    g = rel.run("hooks/gate.py", stdin=payload("pre_tool_call", rel.aj, tool_name="terminal",
                                                tool_input={"command": f"bash {rel.deck}/walter/deploy.sh"}))
    assert g.returncode in (0, 2)
    json.loads(g.stdout or "{}")
    v = rel.run("hooks/verify.py", stdin=payload("pre_verify", rel.aj, extra={
        "platform": "cli", "model": "coder", "coding": True, "attempt": 0, "final_response": "Done.",
        "changed_paths": [str(f) for f in edited(rel)]}))
    assert v.returncode == 0, v.stderr
    json.loads(v.stdout or "{}")
    i = rel.run("hooks/inject.py", stdin=payload("pre_llm_call", rel.aj))
    assert i.returncode == 0, i.stderr
    json.loads(i.stdout or "{}")
    p = rel.run("hooks/enqueue.py", stdin=payload("on_session_end", rel.aj, extra={"final_response": "Done."}))
    assert p.returncode == 0
    reqs = requests(rel.review)
    assert reqs and all(r["data_class"] == "sensitive" for r in reqs), [r["data_class"] for r in reqs]
    assert rel.calls() == []                             # no frontier CLI, no ssh


def test_verify_sanitizer_in_this_repo_fails_closed_with_a_clear_message(rel):
    """C10: verify finds this checkout's root by CONVENTIONS.md + scripts/check-sanitized.sh and runs the shim.
    Without a real-value source the vendored engine exits 2, and verify says what to create. (The engine's own
    lookup ends at <repo>/site.env, the deck-wide order of plan §3, so the planted root file is removed here.)"""
    (rel.aj / "site.env").unlink()
    f = rel.aj / "judge" / "hooks" / "notes.sh"
    write(f, "#!/usr/bin/env bash\necho ok\n", 0o755)
    v = rel.run("hooks/verify.py", stdin=payload("pre_verify", rel.aj, extra={
        "platform": "cli", "model": "coder", "coding": True, "attempt": 0, "final_response": "Done.",
        "changed_paths": [str(f)]}))
    assert v.returncode == 0, v.stderr
    out = json.loads(v.stdout or "{}")
    assert out.get("action") == "continue", out
    assert f"{rel.aj}/scripts/check-sanitized.sh: no real-value source (create ~/.config/deck/site.env)" \
        in out["message"]
    [req] = [r for r in requests(rel.review) if r["kind"] == "completion"]
    assert req["detail"]["verify"]["failed"] == ["check-sanitized"] and req["data_class"] == "sensitive"
    assert rel.calls() == []


def forge(rel, rid, where="queue"):
    req = {"id": rid, "kind": "completion", "session": SESSION, "created": "2026-10-24T03:52:10Z",
           "since": "2026-10-24T03:20:00Z", "changed_paths": [str(p) for p in edited(rel)],
           "claims": "deploy.sh updated", "plan": None, "data_class": "infra", "source_event": "on_session_end",
           "detail": {}}
    for d in ("queue", "evidence", "findings", "acks", "done"):
        (rel.review / d).mkdir(parents=True, exist_ok=True)
    (rel.review / where / f"{rid}.json").write_text(json.dumps(req))
    ev = rel.review / "evidence" / rid
    ev.mkdir()
    (ev / "manifest.json").write_text(json.dumps({"request": req, "data_class": "infra", "artifacts": []}))
    (ev / "hermes-log.txt").write_text(f"2026-10-24T03:30:00Z tool_call write_file {SITE_MARK}\n")
    return req


@pytest.mark.parametrize("pending", [False, True])
def test_runner_refuses_frontier_even_for_a_request_labelled_infra(rel, pending):
    rid = "20261024T035210Z-a1b2c3-completion"
    forge(rel, rid)
    rel.run_env["JUDGE_MAX_ATTEMPTS"] = "1"
    p = rel.run("runner/run_judge.py", *(["--pending"] if pending else [rid]))
    assert rel.calls() == []
    log = (rel.review / "runner.log").read_text()
    assert "frontier refused (nothing sent): site env unresolved" in log
    f = json.loads((rel.review / "findings" / f"{rid}.json").read_text())
    assert f["mode"] == "local" and any("site env unresolved" in n for n in f.get("notes", []))
    assert p.returncode in (0, 1)


def test_rejudge_refuses_frontier(rel, tmp_path):
    rid = "20261024T035210Z-a1b2c3-completion"
    forge(rel, rid, where="done")
    for mode in ("frontier", "frontier-claims"):
        out = tmp_path / f"out-{mode}"
        p = rel.run("runner/rejudge.py", rid, "--mode", mode, "--out", str(out), "--no-budget")
        assert p.returncode == 1
        row = json.loads((out / "summary.json").read_text())["requests"][0]
        assert row.get("refused") and "site env unresolved" in row["error"] and "nothing sent" in row["error"]
        assert not (out / f"{rid}.input.txt").exists()
    assert rel.calls() == []


def test_runaway_watcher_reaches_no_host(rel):
    p = rel.run("watch/runaway.py", "--once", timeout=60)
    assert rel.calls() == []                             # no ssh without a site env
    assert "BACKEND_LAN_IP" in (p.stdout + p.stderr) or p.returncode != 0
