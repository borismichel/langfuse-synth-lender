"""Run a live copilot turn: pull the pinned ``production`` prompt, ask the selected
model, and emit the result as a native agent-graph trace at *now*.

Only the ``answer`` generation is a real model call (its tokens are the real usage);
the surrounding spans are templated from the same scenario content the seeder renders, so
the live trace is shape-identical to the seeded data and lands at the top of the timeline.
The model selector is the demo's lever made tangible: ask the same question on the incumbent
and the candidate.

The emission goes through the **live-emission seam** (``langfuse_synth_core.live``, via
:func:`synth.live.trace.emit_live_turn`) rather than the Spool's ``Ingestor``: a live turn
has no timestamp to supply and is outside the golden gate, which is the line CONTRACT.md
draws between the two writers (portal #211).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from langfuse_synth_core.rng import Rng
from langfuse_synth_core.seed.ingest import assert_demo_project

from ..agent import parse_answer
from ..clients import emitter as get_emitter
from ..clients import langfuse as get_langfuse
from ..clients import llm as get_llm
from ..config import Config
from ..models import AnalystQuestion
from .trace import emit_live_turn

if TYPE_CHECKING:
    from langfuse_synth_core.companion import CompanionAdapter


def _live_answer(cfg: Config, lf, llm, q: AnalystQuestion) -> tuple:
    """Compile the pinned prompt, call the resolved model, parse. Returns
    ``(answer, in_tokens, out_tokens, prompt, latency_ms, messages)`` — the managed prompt
    object rather than its number, because the SDK links a generation to a version by the
    object."""
    name = cfg.certification.prompt_name
    prompt = lf.get_prompt(name, label="production", type="chat", cache_ttl_seconds=0)
    question_json = q.model_dump_json()
    messages = prompt.compile(question=question_json)
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") != "system"] or \
        [{"role": "user", "content": question_json}]
    t0 = time.monotonic()
    result = llm.complete(system=system, messages=turns, temperature=0, max_tokens=700)
    latency_ms = int((time.monotonic() - t0) * 1000)
    chat = [{"role": m.get("role"), "content": m.get("content")} for m in messages]
    return (parse_answer(result.text), result.input_tokens, result.output_tokens,
            prompt, latency_ms, chat)


def submit(cfg: Config, question: AnalystQuestion, model: str | None = None,
           *, adapter: "CompanionAdapter | None" = None,
           log: Callable[[str], None] = print) -> dict:
    """Ask one live question and emit its trace. Returns the answer, the deterministic
    ground truth (for contrast), the prompt version, and a deep link to the trace.

    ``model`` is the copilot's model selector — the incumbent or the candidate under
    certification. ``adapter`` is the Companion Adapter (Spec G · G5, #144): when the live
    Surface hands one in, the ready Langfuse, LLM, and emission clients come from it (the
    adapter owns secret intake and provider resolution, and resolves the selected ``model``);
    without one — the headless ``synth submit`` path — the same clients are built off the core
    resolution module, unchanged."""
    from ..agent import answer_deterministic

    base_url = cfg.target.base_url
    project_id, project_name = assert_demo_project(base_url, cfg.target.project_hint)

    lf = get_langfuse(cfg, adapter=adapter)
    llm = get_llm(model or cfg.certification.incumbent_model, adapter=adapter)
    model = llm.model  # the model actually resolved for the selected provider
    got, in_tok, out_tok, prompt, latency_ms, messages = _live_answer(cfg, lf, llm, question)
    version = getattr(prompt, "version", None)
    log(f"· {model} (prompt v{version}) answered: {got.answer_type} — {got.answer[:90]} ({latency_ms}ms)")

    # The seam stamps wall clock and flushes when the block ends, so the deep link below
    # points at a trace already on its way.
    emitter = get_emitter(cfg, adapter=adapter, environment="production")
    trace_id = emit_live_turn(emitter, cfg, question=question, answer=got,
                              answer_input=messages, answer_usage=(in_tok, out_tok),
                              answer_model=model, prompt=prompt, prompt_version=version,
                              tags=["playground"])

    expected = answer_deterministic(question)
    return {
        "answer": got,
        "expected": expected,
        "model": model,
        "prompt_version": version,
        "trace_id": trace_id,
        "trace_url": f"{base_url.rstrip('/')}/project/{project_id}/traces/{trace_id}",
        "project_name": project_name,
    }


def thumbs_down(cfg: Config, trace_id: str, comment: str,
                *, adapter: "CompanionAdapter | None" = None,
                log: Callable[[str], None] = print) -> dict:
    """Attach an ``analyst_feedback = down`` score (with the analyst's comment) to a
    previously-emitted trace — the same signal that feeds certification-suite intake.
    Idempotent per trace (the score id is derived from the trace id). ``adapter`` supplies the
    ready emission client when the live Surface hands one in (Spec G · G5, #144); otherwise
    it is built off the env, unchanged."""
    base_url = cfg.target.base_url
    project_id, _ = assert_demo_project(base_url, cfg.target.project_hint)
    note = (comment or "").strip() or "analyst flagged this answer"
    s = Rng(cfg.generation.seed).sub("livefeedback", trace_id)
    emitter = get_emitter(cfg, adapter=adapter, environment="production")
    emitter.score("analyst_feedback", "down", data_type="CATEGORICAL", trace_id=trace_id,
                  comment=note, score_id=s.score_id("feedback", trace_id))
    emitter.flush()
    log(f"· thumbs-down logged on {trace_id[:12]}…: {note[:60]}")
    return {"trace_id": trace_id, "comment": note,
            "trace_url": f"{base_url.rstrip('/')}/project/{project_id}/traces/{trace_id}"}
