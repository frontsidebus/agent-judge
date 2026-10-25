"""tools/drill.py with fakes only: a recording stub `claude` (frontier) and a loopback stub gateway (local judge).

The layout is the split's (tests/layout.py): the drill edits a worktree of the deck checkout, its HERMES_HOME and
review dir are under a temp --out, and the site env holds example values plus one planted value (SITE_MARK).
Asserted: every expectation in the report holds, the main-stage frontier input is saved, and nothing the drill
prints or sends contains the planted value, the withheld note or the secret file's content.
"""
from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from layout import REPO, SITE_MARK, Layout, write

DRILL = REPO / "tools" / "drill.py"
FAKE_CLAUDE = """#!{py}
import json, os, sys
rec = {rec!r}
n = len(os.listdir(rec)) + 1
json.dump({{"argv": sys.argv[1:], "stdin": sys.stdin.read()}}, open(os.path.join(rec, "call-%03d.json" % n), "w"))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": '{{"items": []}}',
                  "modelUsage": {{"claude-test-model": {{"outputTokens": 3}}}}}}))
"""


@pytest.fixture
def gateway():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            data = json.dumps({"model": "coder-fast", "choices": [{"message": {"role": "assistant",
                                                                               "content": '{"items": []}'},
                                                                   "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", seen
    srv.shutdown()


@pytest.fixture
def setup(tmp_path, gateway):
    lay = Layout(tmp_path)
    rec = tmp_path / "frontier-calls"
    rec.mkdir()
    claude = write(tmp_path / "bin" / "claude", FAKE_CLAUDE.format(py=sys.executable, rec=str(rec)), 0o755)
    key = write(tmp_path / "local-key", "test-local-key-not-a-secret\n", 0o600)
    url, seen = gateway
    sets = [f"HOME={lay.home}", f"JUDGE_FRONTIER_CMD={claude}", f"JUDGE_LOCAL_URL={url}",
            f"JUDGE_LOCAL_KEY_FILE={key}", "JUDGE_PROBES=0", "JUDGE_LOCAL_MODEL=coder-fast",
            "JUDGE_WINDOW_GRACE_SECONDS=0"]   # the evidence-window wait is real time; not needed here
    return lay, rec, sets, seen


def run_drill(lay, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(lay.home), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-B", str(DRILL), *args], capture_output=True, text=True, env=env,
                          timeout=600, cwd=str(lay.src))


def test_config_check_clean_env(setup):
    lay, _, _, _ = setup
    p = run_drill(lay, "config-check", "--agent-judge-file", str(lay.aj / "judge/README.md"),
                  "--sibling", str(lay.other / "notes.md"))
    assert p.returncode == 0, p.stdout + p.stderr
    r = json.loads(p.stdout)
    assert r["pass"] and r["infra_repos_count"] == 2 and r["mask_values"] > 0
    assert r["class_deck_file"] == "infra" and r["class_agent_judge_file"] == "infra" and r["class_sibling"] == "sensitive"
    assert SITE_MARK not in p.stdout and str(lay.deck) not in p.stdout


def test_config_check_fails_before_infra_repos_are_set(tmp_path):
    lay = Layout(tmp_path, infra_repos=False)
    p = run_drill(lay, "config-check", "--agent-judge-file", str(lay.aj / "judge/README.md"))
    r = json.loads(p.stdout)
    assert p.returncode == 1 and not r["pass"]
    assert r["ok"]["agent_judge_file_infra"] is False and r["ok"]["infra_repos_all_exist"] is False


def test_drill_three_cases_with_fakes(setup, tmp_path):
    lay, rec, sets, seen = setup
    out = tmp_path / "out"
    readme = (lay.wt / "walter/README.md").read_text()
    p = run_drill(lay, "run", "--work", str(lay.wt), "--out", str(out), *sum((["--set", s] for s in sets), []))
    assert p.returncode == 0, p.stdout[-3000:] + p.stderr[-2000:]
    report = json.loads((out / "report.json").read_text())
    assert report["pass"], json.dumps(report, indent=1)
    by = {r["case"]: r for r in report["cases"]}
    infra = by["infra"]["runs"][0]
    assert infra["main"]["mode"] == "frontier" and infra["frontier_input"]["saved"]
    assert infra["frontier_input"]["planted_placeholder_hits"] >= 1
    mixed = by["mixed"]["runs"][0]
    assert by["mixed"]["requests"][0]["data_class"] == "infra" and mixed["frontier_input"]["path_name_hits"] == 0
    sens = by["sensitive"]["runs"][0]
    assert sens["main"]["mode"] == "local" and sens["claims"]["mode"] == "frontier-claims"
    # what reached the fakes: the frontier never saw the planted value, the withheld note or the token
    calls = [json.loads(f.read_text()) for f in sorted(rec.iterdir())]
    assert len(calls) == 3          # infra and mixed: main (input saved as judge-input.txt); sensitive: claims
    sent = "\n".join(c["stdin"] for c in calls)
    assert SITE_MARK not in sent and "DRILL-WITHHELD-" not in sent and "not-a-real-secret-" not in sent
    assert "${BACKEND_LAN_IP}" in sent
    assert seen                      # the local judge ran for the sensitive case (it gets real values on site)
    # saved input == what the stub received for the main stage
    assert infra["frontier_input"]["saved"].endswith("/judge-input.txt")
    saved = (out / infra["frontier_input"]["saved"]).read_text()
    assert any(saved.strip() and saved.strip() in c["stdin"] for c in calls)
    # nothing printed carries the planted value; the work tree is restored
    assert SITE_MARK not in p.stdout and SITE_MARK not in p.stderr
    assert (lay.wt / "walter/README.md").read_text() == readme
    assert not list(lay.wt.glob("drill-only-*.env"))


def test_drill_refuses_a_work_dir_that_is_not_infra(setup, tmp_path):
    lay, _, sets, _ = setup
    write(lay.other / "walter/README.md", "# x\n")
    p = run_drill(lay, "run", "--work", str(lay.other), "--out", str(tmp_path / "o"),
                  *sum((["--set", s] for s in sets), []))
    assert p.returncode == 2 and "not infra-class" in p.stderr


def test_drill_refuses_without_a_site_env(setup, tmp_path):
    lay, _, sets, _ = setup
    lay.judge_env.unlink()
    lay.site_env.unlink()
    p = run_drill(lay, "run", "--work", str(lay.wt), "--out", str(tmp_path / "o"),
                  *sum((["--set", s] for s in sets), []))
    assert p.returncode == 2 and "frontier refused" in p.stderr
