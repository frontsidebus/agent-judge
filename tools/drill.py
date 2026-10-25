#!/usr/bin/env python3
"""tools/drill.py: check the installed judge end to end after a (re)install, without touching real review data.

    tools/drill.py config-check [--judge DIR] [--deck-file PATH] [--agent-judge-file PATH] [--sibling PATH]
    tools/drill.py run --work DIR --out DIR [--judge DIR] [--infra-file REL] [--plant-key KEY] [--no-capture]
                       [--set KEY=VALUE]... [--timeout S]

config-check  runs the judge's config in a child whose environment is PATH and HOME only (what Hermes hooks and
              the systemd units see) and prints booleans and counts: site env resolved, frontier allowed
              (frontier_refusal empty), mask size, JUDGE_REPO_DIR set, JUDGE_INFRA_REPOS count, and the class of
              a deck file, an agent-judge file (default: this checkout's judge/README.md) and optionally a
              sibling that must NOT be infra. Exit 0 when every check holds, 1 otherwise.

run           three synthetic Hermes sessions through the REAL hooks (gate.py, enqueue.py) and the REAL runner
              (run_judge.py), with a fresh HERMES_HOME and JUDGE_REVIEW_DIR under --out, so the owner's queue,
              findings and Hermes state are never touched. The judges are whatever judge.env / site.env
              configure (frontier and local): this makes real model calls. Cases:
                infra      --work/<infra-file> gets a line holding the real value of --plant-key: the frontier
                           input must carry it only as ${KEY}
                mixed      the same edit plus one note in a non-infra sibling dir (<out>/sibling/): the request
                           stays infra with that path WITHHELD; neither its name nor its content may be sent
                sensitive  a secret-shaped file (*.env) in --work: judged locally, plus the frontier claims-only
                           stage; no path, file content or site value may be in claims-input.txt
              --work must be a throwaway checkout of an infra repo (for example `git -C <deck> worktree add
              <tmp>/drill-wt origin/main`); the drill refuses a --work that is not infra-class. It restores the
              infra file and removes its own files from --work afterwards.

              What was sent: run_judge.py saves the main stage's input as evidence/<id>/judge-input.txt (since
              agent-judge 1.0.1), and the drill checks that file. With an older judge that lacks it, the drill
              re-judges each frontier-judged request with `runner/rejudge.py --mode frontier --no-budget`, which
              builds the input with the same code and saves it as <out>/rejudge/<id>.input.txt (a second frontier
              call). --no-capture skips both. The sensitive case's claims stage writes claims-input.txt itself.

Output: <out>/report.json, also printed. It holds counts, classes, modes, judge names and booleans only. Free text
(finding notes) is passed through the site mask and has HOME and --out replaced before it is printed, so no site
value is ever printed. The files under <out> (mode 700) keep what was sent, masked; delete <out> when done.
Exit: 0 every expectation held, 1 an expectation failed, 2 usage or setup error.

--set KEY=VALUE  extra environment for the hooks and runner (tests: JUDGE_FRONTIER_CMD, JUDGE_LOCAL_URL, HOME ...).
                 Applied after JUDGE_* and SITE_ENV are removed from the inherited environment, so by default the
                 judge.env / site.env lookup decides, exactly as for the installed hooks.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
DEFAULT_JUDGE = REPO / "judge"


def die(msg: str, rc: int = 2):
    print(f"drill: {msg}", file=sys.stderr)
    sys.exit(rc)


# ------------------------------------------------------------------------------------------------ config-check
CHECK_CODE = r"""
import json, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from lib import config as C, sitemask as SM
cfg = C.load_config()
out = {"site_env_resolved": bool(cfg.get("SITE_ENV")), "judge_env_present": bool(cfg.get("JUDGE_ENV")),
       "frontier_refusal_empty": C.frontier_refusal(cfg) == "", "judge_mode": cfg.get("JUDGE_MODE"),
       "judge_repo_dir_set": bool(cfg.get("JUDGE_REPO_DIR")),
       "infra_repos_count": len((cfg.get("JUDGE_INFRA_REPOS") or "").split()),
       "infra_repos_existing": sum(os.path.isdir(os.path.expanduser(r))
                                   for r in (cfg.get("JUDGE_INFRA_REPOS") or "").split())}
try:
    out["mask_values"] = len(SM.build(cfg).entries)
except Exception as e:
    out["mask_values"] = -1
for name, p in json.loads(sys.argv[2]).items():
    out["class_" + name] = C.path_class(p, cfg) if p else None
print(json.dumps(out))
"""


def config_check(a) -> int:
    judge = Path(a.judge).resolve()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "")}
    paths = {"deck_file": a.deck_file or "", "agent_judge_file": a.agent_judge_file or str(judge / "README.md"),
             "sibling": a.sibling or ""}
    if not paths["deck_file"]:
        # the deck checkout from the judge's own config (never printed): its CONVENTIONS.md
        p = subprocess.run([sys.executable, "-B", str(judge / "lib/config.py"), "--get", "JUDGE_REPO_DIR"],
                           env=env, capture_output=True, text=True, timeout=30)
        d = p.stdout.strip()
        paths["deck_file"] = os.path.join(d, "CONVENTIONS.md") if d else ""
    p = subprocess.run([sys.executable, "-B", "-c", CHECK_CODE, str(judge), json.dumps(paths)], env=env,
                       capture_output=True, text=True, timeout=60, cwd="/")
    if p.returncode != 0:
        die(f"config check failed to run (exit {p.returncode})")
    r = json.loads(p.stdout)
    r["ok"] = {"site_env_resolved": r["site_env_resolved"], "frontier_refusal_empty": r["frontier_refusal_empty"],
               "judge_repo_dir_set": r["judge_repo_dir_set"],
               "infra_repos_all_exist": r["infra_repos_count"] > 0 and r["infra_repos_existing"] == r["infra_repos_count"],
               "deck_file_infra": r["class_deck_file"] == "infra",
               "agent_judge_file_infra": r["class_agent_judge_file"] == "infra"}
    if paths["sibling"]:
        r["ok"]["sibling_not_infra"] = r["class_sibling"] != "infra"
    r["pass"] = all(r["ok"].values())
    print(json.dumps(r, indent=1))
    return 0 if r["pass"] else 1


# ------------------------------------------------------------------------------------------------ run
class Drill:
    def __init__(self, a):
        self.judge = Path(a.judge).resolve()
        self.work = Path(a.work).resolve()
        self.out = Path(a.out).resolve()
        self.timeout = a.timeout
        self.capture = not a.no_capture
        if self.out.exists() and any(self.out.iterdir()):
            die(f"--out must be new or empty: {self.out}")
        self.out.mkdir(parents=True, exist_ok=True)
        os.chmod(self.out, 0o700)
        self.hermes, self.review = self.out / "hermes", self.out / "review"
        (self.hermes / "logs").mkdir(parents=True, exist_ok=True)
        self.review.mkdir(parents=True, exist_ok=True)
        (self.hermes / "config.yaml").write_text("model: coder\napprovals:\n  mode: manual\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("JUDGE_") and k != "SITE_ENV"}
        for kv in a.set or []:
            k, sep, v = kv.partition("=")
            if not sep or not re.match(r"^[A-Z_][A-Z0-9_]*$", k):
                die(f"--set needs KEY=VALUE: {kv!r}")
            env[k] = v
        env.update(HERMES_HOME=str(self.hermes), JUDGE_REVIEW_DIR=str(self.review), PYTHONDONTWRITEBYTECODE="1")
        self.env = env
        sys.path.insert(0, str(self.judge))
        from lib import config as C  # noqa: E402
        from lib import sitemask as SM  # noqa: E402
        self.C, self.cfg = C, C.load_config(environ=env)
        refusal = C.frontier_refusal(self.cfg)
        if refusal:
            die(f"frontier refused by the judge's own config ({refusal.split(':')[0]}); fix judge.env/site.env first")
        self.smask = SM.build(self.cfg)
        self.key = a.plant_key
        self.planted = self.cfg.get(self.key) or ""
        if not self.planted or not any(e.value == self.planted for e in self.smask.entries):
            die(f"--plant-key {self.key}: not a configured site value the mask knows")
        self.infra_file = self.work / a.infra_file
        if not self.infra_file.is_file():
            die(f"--infra-file not found under --work: {a.infra_file}")
        if C.path_class(str(self.infra_file), self.cfg) != "infra":
            die("--work is not infra-class for this config (use a worktree of JUDGE_REPO_DIR or a JUDGE_INFRA_REPOS "
                "checkout)")
        self.tag = secrets.token_hex(3)
        self.sibling = self.out / "sibling" / f"notes-{self.tag}.md"
        if C.path_class(str(self.sibling), self.cfg) != "sensitive":
            die("--out is not a sensitive location (scratch or infra?); choose another --out")
        self.base = self.infra_file.read_text()

    # -- what may be printed
    def scrub(self, s: str) -> str:
        s = self.smask.mask(s) if hasattr(self.smask, "mask") else self.smask.mask_count(s)[0]
        for p, r in ((str(self.out), "<out>"), (os.environ.get("HOME", "") or "\0", "<home>")):
            s = s.replace(p, r)
        return s

    def hook(self, name: str, payload: Dict) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.judge / "hooks" / f"{name}.py")], input=json.dumps(payload),
                              text=True, capture_output=True, env=self.env, cwd=str(self.work), timeout=90)

    def session(self, sid: str, files, answer: str) -> Dict:
        base = {"session_id": sid, "cwd": str(self.work), "profile": "default", "tool_name": None,
                "tool_input": None}
        rcs = []
        rcs.append(self.hook("enqueue", dict(base, hook_event_name="on_session_start",
                                             extra={"model": "coder", "platform": "cli"})).returncode)
        gate = []
        for i, (path, text) in enumerate(files):
            ti = {"path": str(path), "content": text}
            g = self.hook("gate", dict(base, hook_event_name="pre_tool_call", tool_name="write_file", tool_input=ti,
                                       extra={"tool_call_id": f"c{i}"}))
            try:
                gate.append((json.loads(g.stdout or "{}") or {}).get("action") or "pass")
            except ValueError:
                gate.append(f"rc={g.returncode}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            rcs.append(self.hook("enqueue", dict(base, hook_event_name="post_tool_call", tool_name="write_file",
                                                 tool_input=ti, extra={"status": "ok",
                                                                       "tool_call_id": f"c{i}"})).returncode)
        rcs.append(self.hook("enqueue", dict(base, hook_event_name="on_session_end",
                                             extra={"final_response": answer, "completed": True,
                                                    "interrupted": False})).returncode)
        return {"hook_rcs": rcs, "gate_actions": gate}

    def sent_counts(self, text: str, markers: List[str], names: List[str]) -> Dict:
        left = self.smask.residual(text)
        return {"bytes": len(text), "site_values_left": sum(left.values()),
                "planted_placeholder_hits": text.count("${%s}" % self.key),
                "marker_hits": sum(text.count(m) for m in markers),
                "path_name_hits": sum(text.count(n) for n in names)}

    def run(self) -> Dict:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        env_file = self.work / f"drill-only-{self.tag}.env"
        marker_s, marker_e = f"DRILL-WITHHELD-{self.tag}", f"not-a-real-secret-{self.tag}"
        cases = [
            ("infra", "infra", "frontier",
             [(self.infra_file, self.base + f"\n\nDrill note: the backend answers on {self.planted} for llama-swap.\n")],
             f"I added a note to {self.infra_file.name} with the backend's llama-swap address.", [], []),
            ("mixed", "infra", "frontier",
             [(self.infra_file, self.base + "\n\nDrill note: rotate the gateway key yearly.\n"),
              (self.sibling, f"# notes\n{marker_s}\n")],
             f"I documented key rotation in {self.infra_file.name} and kept a private note.",
             [marker_s], [self.sibling.name]),
            ("sensitive", "sensitive", "local",
             [(env_file, f"DRILL_TOKEN={marker_e}\n")],
             f"I wrote a token file {env_file.name}.", [marker_e], [env_file.name]),
        ]
        rows = []
        try:
            for n, (name, want_class, want_mode, files, answer, markers, names) in enumerate(cases, 1):
                self.infra_file.write_text(self.base)
                sid = f"{stamp}_d{self.tag[:4]}{n}"[:22]
                q = self.review / "queue"
                before = {p.name for p in q.glob("*.json")} if q.is_dir() else set()
                row = {"case": name, "expect": {"data_class": want_class, "main_mode": want_mode}}
                row.update(self.session(sid, files, answer))
                new = sorted(p for p in q.glob("*.json") if p.name not in before) if q.is_dir() else []
                reqs = [json.loads(p.read_text()) for p in new]
                row["requests"] = [{"kind": r["kind"], "data_class": r["data_class"],
                                    "changed_paths": len(r.get("changed_paths") or [])} for r in reqs]
                for r in reqs:
                    t = time.time()
                    rr = subprocess.run([sys.executable, str(self.judge / "runner/run_judge.py"), r["id"]],
                                        env=self.env, capture_output=True, text=True, timeout=self.timeout)
                    run = {"kind": r["kind"], "rc": rr.returncode, "s": round(time.time() - t)}
                    for suffix, label in (("", "main"), (".claims", "claims")):
                        f = self.review / "findings" / f"{r['id']}{suffix}.json"
                        if f.exists():
                            d = json.loads(f.read_text())
                            run[label] = {"mode": d.get("mode"), "judge": d.get("judge"),
                                          "items": len(d.get("items") or []),
                                          "notes": [self.scrub(x)[:160] for x in (d.get("notes") or [])][:4]}
                    ev = self.review / "evidence" / r["id"]
                    claims = ev / "claims-input.txt"
                    if claims.exists():
                        run["claims_input"] = self.sent_counts(claims.read_text(errors="replace"), markers, names)
                    if self.capture and (run.get("main") or {}).get("mode") == "frontier" \
                            and (ev / "judge-input.txt").is_file():
                        sent = ev / "judge-input.txt"
                        run["frontier_input"] = dict(self.sent_counts(sent.read_text(errors="replace"), markers, names),
                                                     rc=0, saved=str(sent.relative_to(self.out)))
                    elif self.capture and (run.get("main") or {}).get("mode") == "frontier":
                        rj = self.out / "rejudge"
                        p = subprocess.run([sys.executable, str(self.judge / "runner/rejudge.py"), r["id"],
                                            "--mode", "frontier", "--out", str(rj), "--no-budget"],
                                           env=self.env, capture_output=True, text=True, timeout=self.timeout)
                        sent = rj / f"{r['id']}.input.txt"
                        run["frontier_input"] = (dict(self.sent_counts(sent.read_text(errors="replace"), markers,
                                                                       names), rc=p.returncode,
                                                      saved=str(sent.relative_to(self.out)))
                                                 if sent.exists() else {"rc": p.returncode, "saved": None})
                    row.setdefault("runs", []).append(run)
                row["ok"] = self.judge_case(row, want_class, want_mode, markers)
                rows.append(row)
        finally:
            self.infra_file.write_text(self.base)
            env_file.unlink(missing_ok=True)
        report = {"cases": rows, "capture": self.capture, "pass": all(all(r["ok"].values()) for r in rows)}
        (self.out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
        return report

    def judge_case(self, row: Dict, want_class: str, want_mode: str, markers: List[str]) -> Dict:
        runs = row.get("runs") or []
        main = [r for r in runs if r["kind"] == "completion"]
        ok = {"one_completion": len(main) == 1,
              "data_class": [q["data_class"] for q in row["requests"] if q["kind"] == "completion"] == [want_class],
              "main_mode": bool(main) and (main[0].get("main") or {}).get("mode") == want_mode,
              "runner_rc_0": all(r["rc"] == 0 for r in runs)}
        for r in runs:
            for k in ("frontier_input", "claims_input"):
                c = r.get(k)
                if c and "bytes" in c:
                    ok[f"{k}_no_site_values"] = c["site_values_left"] == 0
                    ok[f"{k}_no_markers"] = c["marker_hits"] == 0
                    ok[f"{k}_no_withheld_names"] = c["path_name_hits"] == 0
        if row["case"] == "infra" and self.capture:
            fi = (main[0].get("frontier_input") if main else None) or {}
            ok["frontier_input_saved"] = bool(fi.get("saved"))
            ok["planted_value_masked"] = fi.get("planted_placeholder_hits", 0) >= 1
        if want_class == "sensitive":
            ok["claims_stage_frontier"] = bool(main) and (main[0].get("claims") or {}).get("mode") == "frontier-claims"
            ok["claims_input_written"] = bool(main) and "claims_input" in main[0]
        return ok


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/drill.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config-check")
    c.add_argument("--judge", default=str(DEFAULT_JUDGE))
    c.add_argument("--deck-file")
    c.add_argument("--agent-judge-file")
    c.add_argument("--sibling")
    r = sub.add_parser("run")
    r.add_argument("--judge", default=str(DEFAULT_JUDGE))
    r.add_argument("--work", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--infra-file", default="walter/README.md")
    r.add_argument("--plant-key", default="BACKEND_LAN_IP")
    r.add_argument("--no-capture", action="store_true")
    r.add_argument("--set", action="append")
    r.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args(argv)
    if a.cmd == "config-check":
        return config_check(a)
    report = Drill(a).run()
    print(json.dumps(report, indent=1))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
