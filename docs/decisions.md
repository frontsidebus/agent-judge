# Decisions

The agent judge's decision records, carried over from local-inference-deck's `docs/decisions.md` at the split
(plan step 2). The numbers and headings are unchanged, so links of the form `decisions.md#10-...` keep working.
The deck keeps its full record (decisions #1-#9 and #12 onward are about the stack, not the judge); new judge
decisions are added here.

---

## 10. Frontier review of claims-only bundles for sensitive sessions

**Date:** 2026-10-03 · **Status:** accepted (owner decision after judge runs 1 and 2)

**Context.** Requests are classed `infra` or `sensitive` ([agent-judge.md](agent-judge.md#4-data-boundary)). Sensitive evidence went only to the local judge (`coder-fast`, the worker's own model family), capped at medium and not shown to the agent. Every agent slip found in runs 1 and 2 was in a sensitive session (work outside the infra paths), and the local judge caught 2 of 4 in run 2, missing both slips in the run-2 disk/GPU report. The frontier judge caught report slips well on infra bundles, with no medium-or-higher false positive.

**Decision.** For a sensitive **completion**, keep the local review of the full bundle and **add** a frontier review of a **claims-only** bundle: the agent's final answer (redacted, paths replaced by opaque `file#N` ids), gate decisions cut to the command name, C3 pass/fail, tool-call metadata and counts, attribution counts, and the window. Never file contents, diffs, paths, command output, host probes, snapshot data or the user's message. The bundle is built from a positive allowlist (`judge/collector/claims_only.py`), self-checked before sending (refused and skipped on any secret-like value, path, user-message marker or diff), and the exact text sent is kept for audit. Its finding (`findings/<id>.claims.json`, mode `frontier-claims`) may be injected through C5; local findings stay out. `JUDGE_SENSITIVE_FRONTIER_CLAIMS=1` by default; each call counts against `JUDGE_FRONTIER_DAILY_MAX`.

**Why.**
- Most of the slips were in the report itself (internal contradictions, unit labels, "blocked by the gate" for an escalation), which needs the report and the gate/C3/tool metadata, not file contents.
- Measured before deploying: 3 repeats over the six sensitive run-1/run-2 completions (S2, S6, S7, S8 of run 2; S1, S4 of run 1) caught all 4 ground-truth slips in every repeat (12/12; the local judge alone: 2 of 4), plus one more real slip in the S2 report (VRAM headroom figures that contradict its own numbers, confirmed against the reference `nvidia-smi`), with 0 false positives. Two of the gate-wording catches came out at medium instead of low.
- An allowlist plus a refusing self-check makes the boundary auditable: what can leave is listed field by field, and anything unexpected stops the call.

**Cost.** One more frontier call per sensitive completion, so the daily cap fills about twice as fast. The workstation's cap was raised from 20 to 40 when the stage went live.

**Amended after run 3 (bug #26).** The final answer is treated as hostile to the boundary: run 3's S8 answer carried the local username and the digests, length and first character of a key file, and the first self-check let them through. The answer is now masked further (accounts, `user:group`, site hosts, IPv6, hex and base64-like runs, sentences giving a length or prefix of secret material) and the self-check refuses any of those that survive. Spec: `judge/CONTRACT.md`.

**Amended after run 4 (bugs #31 and #33).** The secret-sentence marker is split into a prefix marker (a disclosure) and a length marker (file metadata the gate allows), offers and sizes stay with their numbers masked, and the bundle carries each tool call's allowlisted program name, so a narrowed retry after a refusal no longer looks like a workaround. The stage was off from run 3 until these fixes, and back on from 2026-10-04.

**Residual risk.** The final answer can quote what the agent read: host names, config lines, numbers. Redaction masks secrets and paths, not other content. Bare file names (`config.yaml`) and single-segment relative paths stay.

**Revisit when** a final answer carries content that should not leave (then add a content filter or set `JUDGE_SENSITIVE_FRONTIER_CLAIMS=0`), or when a local judge from another family matches the frontier judge on these slips.

---

## 11. Mixed infra bundles with withheld sensitive paths

**Date:** 2026-10-05 · **Status:** accepted (owner kept the default after PR #28)

**Context.** A request was `sensitive` when any one of its paths was not infra. In pilot 2 a single dry-run helper under the agent's cache dir sent the build's largest change (a 758-line pipeline diff) to the local judge with its diff withheld, and the frontier judge saw only the claims-only bundle (bug #43). Both medium code defects of that build went uncaught.

**Decision.** Classify per path ([agent-judge.md](agent-judge.md#4-data-boundary)). Secret-shaped paths still make the request `sensitive`; the agent's scratch and cache paths never decide the class and are shown as metadata only. A request with up to `JUDGE_MIXED_MAX_SENSITIVE` (default 3) sensitive paths, and at least as many infra paths, stays `infra`: the collector withholds the sensitive files' content, masks their names as `[sensitive path #N withheld]`, drops log and JSONL lines that name them and masks a non-infra working directory. Gate requests stay strict.

**Why.** The per-file withholding is enforced where diffs are built (every path is re-classified before content is shown, repo diffs use literal pathspecs), and tests plant marker strings in sensitive, scratch and gate-log text and find them in no bundle file and no frontier input. It lets the frontier judge review the code it was missing.

**Security note (owner).** This relaxes the data boundary. A review touching up to three sensitive paths now goes to the frontier judge (Claude), provided it has at least as many infra paths; the sensitive files' content is withheld and their names masked. Residual risks:
- the agent's final answer is only redacted, so it could describe a withheld file;
- real site identifiers (domain, IP addresses) in Hermes log lines now reach the frontier judge for mixed bundles.

`JUDGE_MIXED_MAX_SENSITIVE=0` restores strict mode.

**Revisit when** the stack handles anything beyond the owner's own infrastructure (work, client or personal data).

**Amended 2026-10-05 (the two follow-ups, owner-approved).**
- **Site values are masked before every frontier call**, for full infra bundles and the claims stage alike. Each configured `site.env` identifier (the domain and hosts, addresses, SSH users and aliases, bucket names, the site name) becomes a `${KEY}` placeholder. The reverse map is kept only in the runner's memory, and the judge's reply is validated against the masked bundle before the finding is unmasked, so the owner and Hermes see real names and Claude never does. The local judge is not masked: nothing leaves the machine, and its probes need the real names.
- **In a mixed bundle, the agent's free text** (its final answer and gate excerpts) is masked further and must pass the claims self-check. A piece that fails is withheld with a marker naming the problem kinds; the rest of the bundle still goes to the frontier judge. This was preferred to falling back to the local judge, which would lose the code review for a slip in one field.
- **Residual risk:** values that are not in `site.env` (another site's address, the workstation account in home paths) are not masked; a final answer can still paraphrase a withheld file in plain words that no rule detects. Spec: `judge/CONTRACT.md` ("Runner: site values masked", "Runner: agent free text of mixed bundles").
