"""`synth script` — render DEMO_SCRIPT.md and DEMO_MAP.md from run state.

DEMO_SCRIPT.md is the presenter's runbook (the five-row checklist walk, spec v2 §2);
DEMO_MAP.md is the acceptance-criteria artefact (§9.6): checklist row → exact UI path
→ which golden trace/object to open. Both are filled with this run's real ids/dates,
so they can never drift from the seeded data.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Template

from .artifacts import artifact_path
from .config import Config
from .state import REPO_ROOT, RunState

SCRIPT_TEMPLATE = REPO_ROOT / "templates" / "demo_script.md.j2"
MAP_TEMPLATE = REPO_ROOT / "templates" / "demo_map.md.j2"
WALKTHROUGH_TEMPLATE = REPO_ROOT / "templates" / "demo_walkthrough.html.j2"
SCRIPT_OUT = REPO_ROOT / "DEMO_SCRIPT.md"
MAP_OUT = REPO_ROOT / "DEMO_MAP.md"
WALKTHROUGH_OUT = REPO_ROOT / "DEMO_WALKTHROUGH.html"

# The managed judges, created once in the UI (or via the workbench's unstable-API
# path). They grade prose; the deterministic assertions grade the verdicts.
_GROUNDEDNESS_JUDGE = """You are auditing a credit-analyst copilot answer for groundedness.

QUESTION + FILING EXTRACTS: {{input}}
ANSWER UNDER REVIEW:        {{output}}

Score 0.0–1.0: is every factual claim in the answer supported by the lines of the
cited extracts (section ids in `citations`)? Deduct for any claim not present in the
extracts, any figure that contradicts a printed line (apply the unit note; parentheses
are negative), or citations of sections that were not supplied.
Respond as JSON: {"score": <float 0..1>, "reason": "<one sentence naming any unsupported claim>"}"""

_CITATION_JUDGE = """You are auditing a credit-analyst copilot answer for citation coverage.

QUESTION + FILING EXTRACTS: {{input}}
ANSWER UNDER REVIEW:        {{output}}

Score 0.0–1.0: the share of factual claims in the answer that carry a correct
citation (a section id from the supplied extracts). A fluent answer with missing or
unmatched citations scores low even when its content happens to be right.
Respond as JSON: {"score": <float 0..1>, "reason": "<one sentence>"}"""

# Back-compat names for the workbench judge templates.
_COMPLIANCE_JUDGE = _CITATION_JUDGE


def _deep_link(state: RunState, suffix: str, label: str = "open") -> str:
    if not state.project_id:
        return ""
    return f" — [{label}]({state.base_url}/project/{state.project_id}/{suffix})"


def _ui(state: RunState, suffix: str) -> str:
    if not state.project_id:
        return f"(project) → {suffix}"
    return f"{state.base_url}/project/{state.project_id}/{suffix}"


def _html_link(state: RunState, suffix: str, label: str) -> str:
    """An HTML anchor into the project (for the HTML walkthrough), or plain text when
    the project isn't resolved (dry-run). Returned raw — the HTML template is not
    autoescaped."""
    if not state.project_id:
        return label
    return f'<a href="{state.base_url}/project/{state.project_id}/{suffix}">{label}</a>'


def build_context(cfg: Config, state: RunState) -> dict:
    suite = state.suite
    runs = suite.get("runs") or {}
    baseline = next((dict(v, name=k) for k, v in runs.items() if v.get("verdict") == "baseline"), {})
    cand_a = next((dict(v, name=k) for k, v in runs.items()
                   if v.get("verdict") == "pass" and v.get("model") == state.candidate_a_model), {})
    cand_b = next((dict(v, name=k) for k, v in runs.items()
                   if v.get("verdict") == "fail"), {})
    flagged = state.flagged_pending[0] if state.flagged_pending else {}
    return {
        **state.__dict__,
        "suite_info": suite,
        "baseline": baseline,
        "cand_a": cand_a,
        "cand_b": cand_b,
        "flagged": flagged,
        "golden_map": {g["key"]: g for g in state.golden},
        "groundedness_judge": _GROUNDEDNESS_JUDGE,
        "citation_judge": _CITATION_JUDGE,
        "window_days": cfg.generation.window_days,
        "gates": suite.get("gates") or {},
        "scenarios": suite.get("scenarios") or {},
        "ui": lambda suffix: _ui(state, suffix),
        "htmllink": lambda suffix, label: _html_link(state, suffix, label),
        "traces_link": _deep_link(state, "traces"),
        "sessions_link": _deep_link(state, "sessions"),
        "datasets_link": _deep_link(state, "datasets"),
        "prompts_link": _deep_link(state, f"prompts/{state.prompt_name}"),
        "queues_link": _deep_link(state, "annotation-queues"),
        "scores_link": _deep_link(state, "scores"),
        "flagged_link": (_deep_link(state, f"traces/{flagged.get('trace_id', '')}")
                         if flagged else ""),
    }


def render_script(cfg: Config, state: RunState, *, out_path: Path | None = None) -> Path:
    ctx = build_context(cfg, state)
    out_path = out_path or artifact_path("DEMO_SCRIPT.md")
    map_out = out_path.with_name("DEMO_MAP.md") if out_path != SCRIPT_OUT else MAP_OUT
    walkthrough_out = (
        out_path.with_name("DEMO_WALKTHROUGH.html")
        if out_path != SCRIPT_OUT
        else WALKTHROUGH_OUT
    )
    out_path.write_text(Template(SCRIPT_TEMPLATE.read_text()).render(**ctx))
    map_out.write_text(Template(MAP_TEMPLATE.read_text()).render(**ctx))
    # The branded HTML walkthrough — same context, so it can never drift from the MD.
    walkthrough_out.write_text(Template(WALKTHROUGH_TEMPLATE.read_text()).render(**ctx))
    return out_path
