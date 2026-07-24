"""Build one trace's full event tree (spec v2 §5), backdated.

    trace: copilot-turn                       userId=analyst, sessionId=chat
     └─ generation: copilot-turn              (root PLANNER — reads the prompt+question
         │                                     and decides which tools to call: an
         │                                     extended-thinking pass (visible plan +
         │                                     hidden `reasoning` tokens, `plan` role).
         │                                     Envelopes the whole turn like Vercel's
         │                                     ai.streamText; its OWN usage is the
         │                                     planning, the children carry the rest.)
         ├─ RETRIEVER: filings_search         (vector search; ranked hits)
         ├─ TOOL: document_fetch              (fetch matched sections)
         ├─ TOOL: table_extract               (numeric / trend / covenant turns)
         ├─ TOOL: covenant_db_lookup          (covenant turns)
         ├─ TOOL: internal_ratings_lookup     (occasional context call)
         ├─ generation: answer                (THE synthesis generation — carries the
         │                                     real tokens/cost, linked to the exact
         │                                     analyst-copilot prompt version; the
         │                                     score surface attaches here)
         └─ EVENT: escalated_to_human         (when the copilot escalates)

Timestamps walk a cursor forward from the trace timestamp; trace latency is the
critical-path sum. Realism per spec v2 §7: log-normal latencies per observation type
(retrieval ~200–900 ms, generation ~1.5–8 s, slow outliers), 1–3% of traces carry a
tool error **with a retry span**, a handful of failed generations, model-appropriate
tokens/cost, and rich metadata (release/git_sha, prompt_version, filing-type / desk /
language tags).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import Config
from ..content import (
    SCENARIO_OF_KIND,
    answer_messages,
    covenant_db_lookup_io,
    document_fetch_io,
    filings_search_io,
    internal_ratings_lookup_io,
    table_extract_io,
)
from langfuse_synth_core.distributions import cache_split, sample_latency_ms, sample_tokens, text_tokens, tool_latency_ms
from ..models import AnalystQuestion, CopilotAnswer
from langfuse_synth_core.pricing import cost_details, usage_details
from langfuse_synth_core.rng import Rng
from langfuse_synth_core.seed.events import event_event, generation_event, observation_event, trace_event
from .prompts import prompt_text

TRACE_NAME = "copilot-turn"
HISTORY_TOKENS_PER_TURN = 850  # accumulated Q+A added to input per prior turn (multi-turn)
HISTORY_TOKENS_CAP = 16000     # the app trims chat history — deep turns plateau, not explode


def release_sha(prompt_version: int) -> str:
    """Deterministic fake git sha per release era (rides in trace metadata)."""
    return hashlib.blake2b(f"release-v{prompt_version}".encode(), digest_size=4).hexdigest()


@dataclass
class TraceSpec:
    trace_id: str
    timestamp: datetime
    question: AnalystQuestion
    answer: CopilotAnswer
    user_id: str | None
    session_id: str | None
    environment: str
    kind: str  # ambient | golden | flagged | curated | cert_run | batch | live
    question_kind: str = "figure"
    error_mode: str | None = None      # documented failure pattern this answer carries
    model_override: str | None = None  # cert-run traces: the run's model (else the work model)
    prompt_version: int | None = None  # era-resolved analyst-copilot version
    language: str = "en"
    filing: str = "annual-report"
    desk: str = "mid-market"
    token_factor: float = 1.0          # candidate A runs cheaper (tighter outputs)
    turn_index: int = 0
    history: list = field(default_factory=list)  # [(prev_question, prev_answer)] earlier turns
    slow_factor: float = 1.0
    error_step: str | None = None      # tool name to fail (then retried) | "generation"
    ratings_call: bool = False         # occasional internal_ratings_lookup
    answer_obs_id: str = ""            # filled during build, used to attach scores
    tags: list[str] = field(default_factory=list)


class _Cursor:
    def __init__(self, start: datetime):
        self.t = start

    def advance(self, ms: int) -> tuple[datetime, datetime]:
        s = self.t
        e = s + timedelta(milliseconds=ms)
        self.t = e
        return s, e


def _first_token_at(r: Rng, s: datetime, e: datetime) -> datetime:
    return s + (e - s) * r.uniform(0.3, 0.65)


def _needs_table(spec: TraceSpec) -> bool:
    return spec.question_kind in ("figure", "dscr", "covenant", "leverage", "trend")


def _tool(events: list, r: Rng, cur: _Cursor, *, obs_id: str, trace_id: str, name: str,
          obs_type: str, parent_id: str, env: str, inp, out, median_ms: float,
          slow: float, fail: bool, fail_msg: str, meta: dict) -> None:
    """One tool call; on failure emit the errored attempt PLUS a retry span that
    succeeds (spec v2 §7 — errors come with retry spans, not dead ends)."""
    s, e = cur.advance(tool_latency_ms(r, median_ms, 0.45, slow))
    if not fail:
        events.append(observation_event(
            obs_id=obs_id, trace_id=trace_id, name=name, obs_type=obs_type, start=s, end=e,
            parent_id=parent_id, environment=env, input=inp, output=out, metadata=meta))
        return
    events.append(observation_event(
        obs_id=obs_id, trace_id=trace_id, name=name, obs_type=obs_type, start=s, end=e,
        parent_id=parent_id, environment=env, input=inp, output=None,
        level="ERROR", status_message=fail_msg, metadata={**meta, "attempt": 1}))
    s2, e2 = cur.advance(tool_latency_ms(r, median_ms * 1.15, 0.45, slow))
    events.append(observation_event(
        obs_id=obs_id
        [:12] + "ret1", trace_id=trace_id, name=name, obs_type=obs_type, start=s2, end=e2,
        parent_id=parent_id, environment=env, input=inp, output=out,
        metadata={**meta, "attempt": 2, "retry_of": obs_id}))


def build_trace_events(rng: Rng, cfg: Config, spec: TraceSpec, prompt_version: int | None = None,
                       answer_usage: tuple[int, int] | None = None,
                       answer_latency_ms: int | None = None,
                       answer_input: list[dict] | None = None) -> list[dict]:
    """Build the v2 turn tree. Seed path leaves the overrides None (tokens + latency
    sampled; the answer input is the prompt chat turn); the live playground passes the
    real model usage, the measured latency, and the actually-compiled messages."""
    r = rng.sub("trace", spec.trace_id)
    q = spec.question
    env = spec.environment
    tid = spec.trace_id
    cur = _Cursor(spec.timestamp)
    events: list[dict] = []

    work = cfg.model_by_role("work")
    answer_model = spec.model_override or work.name
    answer_pricing = cfg.model_named(answer_model)
    pver = spec.prompt_version or prompt_version or cfg.certification.production_version

    history_tokens = 0
    if spec.turn_index:
        hr = r.sub("history", spec.turn_index)
        history_tokens = min(int(spec.turn_index * hr.lognormal(HISTORY_TOKENS_PER_TURN, 0.25)),
                             HISTORY_TOKENS_CAP)

    tags = list(spec.tags)
    tags += [f"filing-type:{spec.filing}", f"desk:{spec.desk}", f"language:{spec.language}"]
    if spec.environment == "staging":
        tags.append("staging")

    agent_id = r.obs_id("agent", tid)
    search_call_id = r.obs_id("toolcall_search", tid)
    fetch_call_id = r.obs_id("toolcall_fetch", tid)
    table_call_id = r.obs_id("toolcall_table", tid)
    cov_call_id = r.obs_id("toolcall_cov", tid)
    tool_calls = [{"id": search_call_id, "name": "filings_search"},
                  {"id": fetch_call_id, "name": "document_fetch"}]

    # -- planning pass --------------------------------------------------------
    # The agent reasons over the prompt + question and decides which tools to call
    # BEFORE any retrieval runs. The root generation (emitted last, enveloping the
    # whole turn) carries this planning/decision usage; tools begin once the plan
    # is decided. `plan_start`/`plan_done` bound the deciding window.
    plan_start = spec.timestamp
    _, plan_done = cur.advance(sample_latency_ms(r, "plan", spec.slow_factor))

    # -- filings_search (RETRIEVER) ----------------------------------------
    sin, sout = filings_search_io(q, spec.filing)
    _tool(events, r, cur, obs_id=r.obs_id("search", tid), trace_id=tid, name="filings_search",
          obs_type="RETRIEVER", parent_id=agent_id, env=env, inp=sin, out=sout,
          median_ms=420, slow=spec.slow_factor, fail=spec.error_step == "filings_search",
          fail_msg="filings index timeout",
          meta={"retriever": "vector_search", "toolCallId": search_call_id})

    # -- document_fetch (TOOL) ----------------------------------------------
    fin, fout = document_fetch_io(q)
    _tool(events, r, cur, obs_id=r.obs_id("fetch", tid), trace_id=tid, name="document_fetch",
          obs_type="TOOL", parent_id=agent_id, env=env, inp=fin, out=fout,
          median_ms=260, slow=spec.slow_factor, fail=spec.error_step == "document_fetch",
          fail_msg="document store 503",
          meta={"tool": "document_store", "toolCallId": fetch_call_id})

    # -- table_extract (TOOL — numeric / trend / covenant turns) ------------
    # Trend questions extract per filing: one table_extract call per excerpt (the
    # agentic span-hierarchy showpiece, spec v2 golden trace 4).
    if _needs_table(spec):
        tool_calls.append({"id": table_call_id, "name": "table_extract"})
        if spec.question_kind == "trend" and len(q.excerpts) > 1:
            for xi, exc in enumerate(q.excerpts):
                sub_q = q.model_copy(update={"excerpts": [exc]})
                tin, tout = table_extract_io(sub_q)
                _tool(events, r, cur, obs_id=r.obs_id("table", tid, xi), trace_id=tid,
                      name="table_extract", obs_type="TOOL", parent_id=agent_id, env=env,
                      inp=tin, out=tout, median_ms=340, slow=spec.slow_factor,
                      fail=spec.error_step == "table_extract" and xi == 0,
                      fail_msg="table extraction failed on scanned page",
                      meta={"tool": "table_extractor", "toolCallId": table_call_id,
                            "filing_section": exc.section_id})
        else:
            tin, tout = table_extract_io(q)
            _tool(events, r, cur, obs_id=r.obs_id("table", tid), trace_id=tid,
                  name="table_extract", obs_type="TOOL", parent_id=agent_id, env=env,
                  inp=tin, out=tout, median_ms=340, slow=spec.slow_factor,
                  fail=spec.error_step == "table_extract",
                  fail_msg="table extraction failed on scanned page",
                  meta={"tool": "table_extractor", "toolCallId": table_call_id})

    # -- covenant_db_lookup (TOOL — covenant turns) --------------------------
    if spec.question_kind in ("covenant", "dscr", "leverage"):
        tool_calls.append({"id": cov_call_id, "name": "covenant_db_lookup"})
        cin, cout = covenant_db_lookup_io(rng, q)
        _tool(events, r, cur, obs_id=r.obs_id("covdb", tid), trace_id=tid,
              name="covenant_db_lookup", obs_type="TOOL", parent_id=agent_id, env=env,
              inp=cin, out=cout, median_ms=110, slow=spec.slow_factor,
              fail=spec.error_step == "covenant_db_lookup", fail_msg="covenant DB timeout",
              meta={"tool": "covenant_db", "toolCallId": cov_call_id})

    # -- internal_ratings_lookup (TOOL — occasional context call) ------------
    if spec.ratings_call:
        rin, rout = internal_ratings_lookup_io(rng, q)
        _tool(events, r, cur, obs_id=r.obs_id("rating", tid), trace_id=tid,
              name="internal_ratings_lookup", obs_type="TOOL", parent_id=agent_id, env=env,
              inp=rin, out=rout, median_ms=95, slow=spec.slow_factor, fail=False,
              fail_msg="", meta={"tool": "ratings_service"})

    # -- the generation -------------------------------------------------------
    gen_failed = spec.error_step == "generation"
    s, e = cur.advance(answer_latency_ms if answer_latency_ms is not None
                       else sample_latency_ms(r, "work", spec.slow_factor))
    if answer_input is None:  # seed: the prompt chat turn, compiled like live
        answer_input = answer_messages(prompt_text(pver), q, spec.history)
    if answer_usage is not None:  # live: real token counts, no cache split
        ti, ot, cr, cc = answer_usage[0], answer_usage[1], 0, 0
    else:
        it, ot, _ = sample_tokens(r, "work", visible_input=text_tokens(answer_input),
                                  visible_output=text_tokens(spec.answer.model_dump()),
                                  context_tokens=history_tokens)
        ot = max(1, int(ot * spec.token_factor))
        ti, cr, cc = cache_split(r, "work", it)
    ans_id = r.obs_id("answer", tid)
    spec.answer_obs_id = ans_id
    events.append(generation_event(
        obs_id=ans_id, trace_id=tid, name="answer", start=s, end=e, parent_id=agent_id,
        completion_start=_first_token_at(r, s, e),
        model=answer_model,
        usage_details=usage_details(ti, ot if not gen_failed else 1, cr, cc),
        cost_details=cost_details(answer_pricing, ti, ot if not gen_failed else 1, cr, cc),
        environment=env,
        input=answer_input,
        output=None if gen_failed else spec.answer.model_dump(),
        level="ERROR" if gen_failed else None,
        status_message="upstream model timeout after 30s" if gen_failed else None,
        model_parameters={"temperature": 0}, metadata={"tool_calls": tool_calls},
        prompt_name=cfg.certification.prompt_name if pver else None,
        prompt_version=pver))

    # -- escalation event ------------------------------------------------------
    if spec.answer.answer_type == "escalated" and not gen_failed:
        events.append(event_event(
            obs_id=r.obs_id("escalate", tid), trace_id=tid, name="escalated_to_human",
            start=cur.t, parent_id=agent_id, environment=env,
            metadata={"route": "senior_credit_officer", "reason": "conflicting sources"},
            input={"case_id": q.case_id}, output={"queued": True}))

    # -- root planner generation (spans the whole turn) -----------------------
    # The agent's planning/decision call: reads the prompt + question and decides
    # which tools to invoke — an extended-thinking pass (visible plan + hidden
    # `reasoning` tokens, the `plan` role profile). Like Vercel's ai.streamText it
    # envelopes the entire turn; its OWN usage is the planning, while the nested
    # tools and the `answer` generation carry the rest. The real synthesis tokens
    # live on `answer`, so the trace aggregates two genuine calls (plan + synthesis)
    # without double-counting. Scores still attach to `answer` (spec.answer_obs_id).
    plan_output = {"decision": "call_tools", "tool_calls": tool_calls}
    if answer_usage is not None:   # live: no separately-measured planner call — keep it light
        p_in, p_out, p_reason = text_tokens(answer_input), text_tokens(plan_output), 0
        pti, pcr, pcc = p_in, 0, 0
    else:
        p_in, p_out, p_reason = sample_tokens(
            r, "plan", visible_input=text_tokens(answer_input),
            visible_output=text_tokens(plan_output), context_tokens=history_tokens)
        p_out = max(1, int(p_out * spec.token_factor))
        p_reason = int(p_reason * spec.token_factor)
        pti, pcr, pcc = cache_split(r, "plan", p_in)
    events.insert(0, generation_event(
        obs_id=agent_id, trace_id=tid, name="copilot-turn",
        start=plan_start, end=cur.t,
        completion_start=_first_token_at(r, plan_start, plan_done),
        model=answer_model,
        usage_details=usage_details(pti, p_out, pcr, pcc, reasoning=p_reason),
        cost_details=cost_details(answer_pricing, pti, p_out, pcr, pcc, reasoning=p_reason),
        environment=env,
        input=answer_input,
        output=plan_output,
        model_parameters={"temperature": 1, "thinking": "enabled", "thinking_budget_tokens": 2048},
        metadata={"tool_calls": tool_calls, "case_id": q.case_id, "step": "plan"},
        prompt_name=cfg.certification.prompt_name if pver else None,
        prompt_version=pver))

    # -- trace shell -----------------------------------------------------------
    events.insert(0, trace_event(
        trace_id=tid, timestamp=spec.timestamp, name=TRACE_NAME, user_id=spec.user_id,
        session_id=spec.session_id, tags=tags or None, environment=env,
        input=q.model_dump(),
        output={"error": "generation failed"} if gen_failed else spec.answer.model_dump(),
        metadata={"kind": spec.kind, "question_kind": spec.question_kind,
                  "scenario": SCENARIO_OF_KIND.get(spec.question_kind, "numeric_lookup"),
                  "case_id": q.case_id, "borrower": q.borrower,
                  "prompt_version": pver, "release": f"copilot-{release_sha(pver)}",
                  "git_sha": release_sha(pver),
                  **({"error_mode": spec.error_mode} if spec.error_mode else {})}))
    return events
