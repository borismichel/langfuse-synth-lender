"""Roles, sign-off, and the evidence pack.

Roles are a demo-grade switcher (cookie; Builder / Validator / Approver — no real
auth, which WORKBENCH.md states plainly): Builders design specs, anyone runs,
**only an Approver signs off**. Sign-off is recorded three ways: in the workbench
run record, as a ``reviewer_verdict`` score on a sample run trace, and as a
COMPLETED item in the ``certification-signoff`` annotation queue — so Langfuse
holds the approval evidence, not just the tool.

The evidence pack is the run-scoped dossier: spec (canonical JSON + hash), release,
evaluator code SHAs, per-suite/slice results, gate verdicts, Langfuse run links, and
the sign-off record. Download is enabled only after sign-off (4-eyes before evidence
leaves the building).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import Config
from .results import WorkbenchRun, aggregates, save_run

ROLES = ("builder", "validator", "approver")
ROLE_COOKIE = "wb_role"


def can_sign(role: str) -> bool:
    return role == "approver"


def sign_off(cfg: Config, run: WorkbenchRun, *, role: str, name: str,
             note: str) -> tuple[bool, str]:
    if not can_sign(role):
        return False, "only an Approver can sign off (switch role on the overview page)"
    if run.state != "done":
        return False, f"run is {run.state} — only finished runs can be signed off"
    run.signoff = {"by": name or "approver", "role": role, "note": note,
                   "at": datetime.now(timezone.utc).isoformat()}
    save_run(cfg, run)
    err = _record_in_langfuse(cfg, run)
    return True, ("" if not err else f"recorded locally; Langfuse evidence write failed: {err}")


def _record_in_langfuse(cfg: Config, run: WorkbenchRun) -> str:
    """Queue item + reviewer_verdict score on a sample run trace (best-effort)."""
    sample = next((r for r in run.rows if r.get("trace_id")), None)
    if sample is None:
        return "no run trace available"
    try:
        from ..rng import Rng
        from ..seed.annotation import add_queue_item, ensure_queue, score_config_ids
        from ..seed.events import score_event
        from ..seed.ingest import Ingestor
        from ..seed.scores import REVIEW_QUEUE_CONFIGS

        base = cfg.target.base_url
        qcfg = cfg.certification.queue
        qid = ensure_queue(base, qcfg.name, "Human review feeding the certification suite.",
                           score_config_ids(base, REVIEW_QUEUE_CONFIGS))
        add_queue_item(base, qid, sample["trace_id"], "COMPLETED")
        s = Rng(cfg.generation.seed).sub("wbsignoff", run.run_id)
        ev = score_event(
            score_id=s.score_id("signoff", sample["trace_id"]), name="reviewer_verdict",
            value="confirmed", data_type="CATEGORICAL",
            timestamp=datetime.now(timezone.utc), trace_id=sample["trace_id"],
            environment="default",
            comment=(f"Certification sign-off: {run.signoff.get('by')} on {run.spec_ref} "
                     f"(spec {run.spec_hash[:12]}…). {run.signoff.get('note', '')}".strip()))
        ing = Ingestor.from_env(base)
        ing.add(ev)
        ing.flush()
        return ""
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Evidence pack
# ---------------------------------------------------------------------------
def evidence_markdown(cfg: Config, run: WorkbenchRun) -> str:
    spec = run.spec
    aggs = aggregates(run)
    lines = [
        f"# Evidence Pack — {run.spec_ref}",
        f"**{cfg.workbench.brand} · Validation Workbench**",
        "",
        "| | |",
        "|---|---|",
        f"| Run | `{run.run_id}` ({run.started[:19]} → {run.finished[:19]}) |",
        f"| Release | `{run.release.get('model')}` + `{run.release.get('prompt_name')}` "
        f"v{run.release.get('prompt_version')} (temp {run.release.get('temperature', 0)}) |",
        f"| Spec hash | `{run.spec_hash}` |",
        f"| Dataset freeze | {spec.get('freeze_dataset_version') or '— (latest items)'} |",
        f"| Sign-off | {run.signoff.get('by', '**UNSIGNED**')}"
        + (f" ({run.signoff.get('role')}) at {run.signoff.get('at', '')[:19]}"
           if run.signoff else "") + " |",
        "",
        "## Evaluator code fingerprints",
        "Deterministic checks are version-controlled code; these SHA-256 fingerprints",
        "identify the exact logic that produced every verdict below.",
        "",
    ]
    for name, sha in (run.evaluator_shas or {}).items():
        lines.append(f"- `{name}` — `{sha}`")
    lines += ["", "## Results", ""]
    for ds, a in aggs.items():
        lines.append(f"### {ds} — {a['passed']}/{a['n']} ({a['rate']:.1%})")
        lines.append("")
        lines.append("| Slice | Passed | Rate |")
        lines.append("|---|---|---|")
        for sl, s in sorted(a["slices"].items()):
            lines.append(f"| `{sl}` | {s['passed']}/{s['n']} | {s['rate']:.0%} |")
        lines.append("")
    lines += ["## Gate verdicts", ""]
    for g in run.gates:
        ok = "PASS" if g["ok"] else "FAIL"
        lines.append(f"- **{g['dataset']}**: {ok} — {g['pass_rate']:.1%} vs ≥{g['threshold']:.0%}")
        for sl, d in (g.get("slice_detail") or {}).items():
            lines.append(f"    - `{sl}`: {d['rate']:.0%} vs ≥{d['threshold']:.0%} "
                         f"({'ok' if d['ok'] else 'FAIL'})")
    lines += ["", "## Failures (with reasons)", ""]
    fails = [r for r in run.rows if not r["passed"]]
    if not fails:
        lines.append("None — all items passed their gate checks.")
    for r in fails:
        lines.append(f"- `{r['dataset']}` / `{r['slice']}` / item `{r['item_id']}`: "
                     f"{r['detail']}" + (f" — [trace]({r['trace_url']})" if r["trace_url"] else ""))
    lines += ["", "## Langfuse records", ""]
    for lr in run.langfuse_runs:
        url = lr.get("runs_url", "")
        link = f" — [open]({url})" if url else ""
        lines.append(f"- Dataset Run `{lr['run_name']}` on `{lr['dataset']}`{link}")
    lines += ["", "## Experiment spec (canonical)", "", "```json",
              json.dumps(spec, indent=2, sort_keys=True), "```", "",
              "*Generated by the Validation Workbench; regenerable from the run record.*"]
    return "\n".join(lines) + "\n"
