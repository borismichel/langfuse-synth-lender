"""The live copilot turn, emitted at *now* through the live-emission seam (portal #211).

A playground turn produces the same shape the seeded pool is made of, so a live answer
lands at the top of the same timeline and renders the same way:

    copilot-turn                          (the trace: its root observation)
     └─ generation: copilot-turn          (the planning pass that decides which tools run)
         ├─ RETRIEVER: filings_search
         ├─ TOOL: document_fetch
         ├─ generation: answer            ← the one real model call
         └─ EVENT: escalated_to_human     (when the copilot escalates)

What it does not share with the pool is the writer. ``seed/traces.py`` builds backdated
OTLP spans for the Spool — producer-supplied timestamps, producer-minted ids, byte-compared
against a golden — and a live turn has none of that: it happens now and it calls a model.
So it rides ``langfuse_synth_core.live.emit``, which takes no timestamp by design
(CONTRACT.md, the determinism line).

The two writers share the *scenario* instead: the tool IO below comes from
``synth.content``, the same module the seeder renders from. A live turn asks one question
with no history and no injected failure, so the branches the seeder carries for trend
questions, covenant lookups, error steps and retries are not reachable here — that is what
makes this the short version of the same tree rather than a second copy of it.
"""
from __future__ import annotations

from typing import Any, Callable

from langfuse_synth_core.distributions import text_tokens
from langfuse_synth_core.pricing import cost_details, usage_details
from langfuse_synth_core.rng import Rng

from ..config import Config
from ..content import document_fetch_io, filings_search_io
from ..models import AnalystQuestion, CopilotAnswer

TRACE_NAME = "copilot-turn"


def emit_live_turn(emitter: Any, cfg: Config, *, question: AnalystQuestion,
                   answer_input: list[dict], run_answer: Callable[[], tuple],
                   answer_model: str, prompt: Any = None,
                   prompt_version: int | None = None,
                   filing: str = "annual-report", desk: str = "mid-market",
                   language: str = "en", user_id: str = "analyst_playground",
                   environment: str = "production",
                   tags: list[str] | None = None) -> tuple[str, CopilotAnswer]:
    """Emit the turn's tree and return ``(trace_id, answer)``.

    ``run_answer`` is **called inside the `answer` generation**, and returns
    ``(answer, input_tokens, output_tokens)``. That is not a style choice: the seam stamps
    wall clock and offers no start-time parameter, so a model call made before the span is
    opened lands as a generation of roughly zero milliseconds — beside a seeded pool whose
    latencies are realistic, which is precisely the column a presenter opens this trace to
    look at. The call has to happen while the span is open, so the caller hands it in.

    ``answer_input`` is the chat turn the model sees and ``prompt`` the managed prompt
    object — the SDK links a generation to its version by that object, which is what makes
    "which release answered this?" clickable in the UI.
    """
    r = Rng(cfg.generation.seed).sub("live", question.case_id)
    pricing = cfg.model_named(answer_model)

    all_tags = list(tags or []) + [f"filing-type:{filing}", f"desk:{desk}",
                                   f"language:{language}"]

    with emitter.trace(TRACE_NAME, user_id=user_id, environment=environment, tags=all_tags,
                       input=question.model_dump(),
                       metadata={"kind": "live", "question_kind": "live",
                                 "prompt_version": prompt_version}) as trace:
        search_call_id = r.obs_id("toolcall_search", trace.id)
        fetch_call_id = r.obs_id("toolcall_fetch", trace.id)
        tool_calls = [{"id": search_call_id, "name": "filings_search"},
                      {"id": fetch_call_id, "name": "document_fetch"}]
        plan_output = {"decision": "call_tools", "tool_calls": tool_calls}

        # The planning pass envelopes the whole turn, exactly as it does in the pool: its
        # own usage is the deciding, and `answer` below carries the synthesis. Two genuine
        # calls, no double-counting.
        p_in, p_out = text_tokens(answer_input), text_tokens(plan_output)
        with trace.generation(
                TRACE_NAME, model=answer_model,
                usage=usage_details(p_in, p_out, 0, 0),
                cost=cost_details(pricing, p_in, p_out, 0, 0),
                input=answer_input, prompt=prompt,
                model_parameters={"temperature": 1, "thinking": "enabled",
                                  "thinking_budget_tokens": 2048},
                metadata={"tool_calls": tool_calls, "case_id": question.case_id,
                          "step": "plan"}) as planner:
            search_in, search_out = filings_search_io(question, filing)
            with planner.observation("filings_search", as_type="retriever", input=search_in,
                                     metadata={"retriever": "vector_search",
                                               "toolCallId": search_call_id}) as search:
                search.update(output=search_out)

            fetch_in, fetch_out = document_fetch_io(question)
            with planner.observation("document_fetch", as_type="tool", input=fetch_in,
                                     metadata={"tool": "document_store",
                                               "toolCallId": fetch_call_id}) as fetch:
                fetch.update(output=fetch_out)

            with planner.generation("answer", model=answer_model, input=answer_input,
                                    model_parameters={"temperature": 0},
                                    metadata={"tool_calls": tool_calls},
                                    prompt=prompt) as gen:
                answer, in_tok, out_tok = run_answer()
                gen.update(output=answer.model_dump(),
                           usage_details=usage_details(in_tok, out_tok, 0, 0),
                           cost_details=cost_details(pricing, in_tok, out_tok, 0, 0))

            if answer.answer_type == "escalated":
                with planner.event("escalated_to_human", input={"case_id": question.case_id},
                                   metadata={"route": "senior_credit_officer",
                                             "reason": "conflicting sources"}) as escalation:
                    escalation.update(output={"queued": True})

            planner.update(output=plan_output)
        trace.update(output=answer.model_dump())
        trace_id = trace.id
    return trace_id, answer
