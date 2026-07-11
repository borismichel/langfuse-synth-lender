"""`synth memo` — render CERT_MEMO.md, the model-validation dossier.

Filled from ``.synth_state.json`` (which ``synth certify`` and the workbench update),
so the dossier can never disagree with the data in Langfuse: scope (release = model +
prompt version + params), the three-run comparison with per-scenario gates, the
human-review record, and the verdict.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Template

from .artifacts import artifact_path
from .config import Config
from .state import REPO_ROOT, RunState

TEMPLATE_PATH = REPO_ROOT / "templates" / "cert_memo.md.j2"
OUTPUT_PATH = REPO_ROOT / "CERT_MEMO.md"


def build_context(cfg: Config, state: RunState) -> dict:
    suite = state.suite
    runs = []
    for run_name, r in sorted((suite.get("runs") or {}).items(),
                              key=lambda kv: (kv[1].get("date", ""), kv[0])):
        runs.append({"run_name": run_name, **r})

    def link(suffix: str) -> str:
        if not state.project_id:
            return ""
        return f"{state.base_url}/project/{state.project_id}/{suffix}"

    return {
        **state.__dict__,
        "suite_info": suite,
        "runs_list": runs,
        "gates": suite.get("gates") or {},
        "candidate_a": next((r for r in runs if r.get("verdict") == "pass"), {}),
        "candidate_b": next((r for r in runs if r.get("verdict") == "fail"), {}),
        "baseline": next((r for r in runs if r.get("verdict") == "baseline"), {}),
        "live_runs": [r for r in runs if r.get("verdict") == "live"],
        "workbench_runs": _workbench_runs(cfg),
        "datasets_link": link("datasets"),
        "traces_link": link("traces"),
        "prompts_link": link(f"prompts/{state.prompt_name}"),
    }


def _workbench_runs(cfg: Config) -> list[dict]:
    try:
        from .workbench.results import list_runs

        out = []
        for r in list_runs(cfg):
            if r.state != "done":
                continue
            out.append({
                "run_id": r.run_id, "spec_ref": r.spec_ref, "spec_hash": r.spec_hash,
                "model": r.release.get("model", ""),
                "prompt_version": r.release.get("prompt_version", ""),
                "gates_ok": all(g.get("ok") for g in r.gates),
                "signed_by": r.signoff.get("by", "") if r.signoff else "",
                "signed_at": (r.signoff.get("at", "") or "")[:19] if r.signoff else "",
            })
        return out
    except Exception:  # noqa: BLE001 — memo renders fine without the workbench
        return []


def render_memo(cfg: Config, state: RunState, *, out_path: Path | None = None) -> Path:
    out_path = out_path or artifact_path("CERT_MEMO.md")
    template = Template(TEMPLATE_PATH.read_text())
    out_path.write_text(template.render(**build_context(cfg, state)))
    return out_path
