"""Run a live copilot turn: pull the pinned ``production`` prompt, ask the selected
model, and emit the result as a native agent-graph trace at *now*.

Only the ``answer`` generation is a real model call (its tokens are the real usage);
the surrounding spans are templated/computed exactly like the seed, so the live trace
is shape-identical to the seeded data and lands at the top of the timeline. The model
selector is the demo's lever made tangible: ask the same question on the incumbent and
the candidate.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from ..agent import parse_answer
from ..config import Config
from ..models import AnalystQuestion
from ..rng import Rng
from ..seed.events import score_event
from ..seed.ingest import Ingestor, assert_demo_project
from ..seed.traces import TraceSpec, build_trace_events


def _live_answer(cfg: Config, lf, anth, q: AnalystQuestion, model: str) -> tuple:
    """Compile the pinned prompt, call ``model``, parse. Returns
    ``(answer, in_tokens, out_tokens, prompt_version, latency_ms, messages)``."""
    name = cfg.certification.prompt_name
    prompt = lf.get_prompt(name, label="production", type="chat", cache_ttl_seconds=0)
    question_json = q.model_dump_json()
    messages = prompt.compile(question=question_json)
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") != "system"] or \
        [{"role": "user", "content": question_json}]
    t0 = time.monotonic()
    resp = anth.messages.create(model=model, system=system, messages=turns,
                                temperature=0, max_tokens=700)
    latency_ms = int((time.monotonic() - t0) * 1000)
    text = "".join(b.text for b in resp.content if b.type == "text")
    chat = [{"role": m.get("role"), "content": m.get("content")} for m in messages]
    return (parse_answer(text), resp.usage.input_tokens, resp.usage.output_tokens,
            getattr(prompt, "version", None), latency_ms, chat)


def submit(cfg: Config, question: AnalystQuestion, model: str | None = None,
           *, log: Callable[[str], None] = print) -> dict:
    """Ask one live question and emit its trace. Returns the answer, the deterministic
    ground truth (for contrast), the prompt version, and a deep link to the trace."""
    from ..agent import answer_deterministic
    from ..lfclient import get_anthropic, get_langfuse

    base_url = cfg.target.base_url
    project_id, project_name = assert_demo_project(base_url, cfg.target.project_hint)
    model = model or cfg.certification.incumbent_model

    lf = get_langfuse(cfg)
    anth = get_anthropic()
    got, in_tok, out_tok, version, latency_ms, messages = _live_answer(cfg, lf, anth, question, model)
    log(f"· {model} (prompt v{version}) answered: {got.answer_type} — {got.answer[:90]} ({latency_ms}ms)")

    now = datetime.now(timezone.utc)
    trace_id = uuid.uuid4().hex
    spec = TraceSpec(
        trace_id=trace_id, timestamp=now, question=question, answer=got,
        user_id="analyst_playground", session_id=None, environment="production",
        kind="live", question_kind="live", model_override=model, tags=["playground"])
    events = build_trace_events(Rng(cfg.generation.seed), cfg, spec, version,
                                answer_usage=(in_tok, out_tok),
                                answer_latency_ms=latency_ms, answer_input=messages)
    ing = Ingestor.from_env(base_url)
    ing.extend(events)
    ing.flush()

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
                *, log: Callable[[str], None] = print) -> dict:
    """Attach an ``analyst_feedback = down`` score (with the analyst's comment) to a
    previously-emitted trace — the same signal that feeds certification-suite intake.
    Idempotent per trace (the score id is derived from the trace id)."""
    base_url = cfg.target.base_url
    project_id, _ = assert_demo_project(base_url, cfg.target.project_hint)
    note = (comment or "").strip() or "analyst flagged this answer"
    s = Rng(cfg.generation.seed).sub("livefeedback", trace_id)
    ev = score_event(score_id=s.score_id("feedback", trace_id), name="analyst_feedback",
                     value="down", data_type="CATEGORICAL",
                     timestamp=datetime.now(timezone.utc),
                     trace_id=trace_id, environment="production", comment=note)
    ing = Ingestor.from_env(base_url)
    ing.add(ev)
    ing.flush()
    log(f"· thumbs-down logged on {trace_id[:12]}…: {note[:60]}")
    return {"trace_id": trace_id, "comment": note,
            "trace_url": f"{base_url.rstrip('/')}/project/{project_id}/traces/{trace_id}"}
