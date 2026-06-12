"""Scores & configs (spec v2 §5): **every score-method type on one surface**, with
one name vocabulary across production traces and experiment runs:

- ``citation_format``        — DETERMINISTIC assertion, every answered turn (~99% pass)
- ``numeric_accuracy``       — DETERMINISTIC assertion on sampled numeric turns
- ``escalation_correctness`` — DETERMINISTIC assertion on out-of-scope turns
- ``groundedness``           — LLM judge, thin sample (carries the optional quality-dip
                               ambience tied to the v6 prompt era)
- ``citation_coverage``      — LLM judge, thin sample
- ``analyst_feedback``       — user feedback, sparse (8–15% of traces)
- human annotation: the review queue binds the SAME criteria configs above —
  reviewers score groundedness/citation/numeric scales to CREATE ground truth

Realism (spec v2 §7): skewed distributions, occasional missing scores, judge–human
agreement ~85–90% with visible disagreements.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..rng import Rng
from .events import score_event

# Score configs to create up front (POST /api/public/score-configs).
SCORE_CONFIGS: list[dict] = [
    {"name": "numeric_accuracy", "dataType": "CATEGORICAL",
     "categories": [{"label": "pass", "value": 1}, {"label": "fail", "value": 0}],
     "description": "DETERMINISTIC assertion: extracted figures exact (EUR ints), ratios ±0.02, answer behaviour correct."},
    {"name": "citation_format", "dataType": "CATEGORICAL",
     "categories": [{"label": "pass", "value": 1}, {"label": "fail", "value": 0}],
     "description": "DETERMINISTIC assertion: citations present and well-formed (section ids match the supplied extracts)."},
    {"name": "escalation_correctness", "dataType": "CATEGORICAL",
     "categories": [{"label": "pass", "value": 1}, {"label": "fail", "value": 0}],
     "description": "DETERMINISTIC assertion: declined / abstained / escalated exactly when policy requires."},
    {"name": "groundedness", "dataType": "NUMERIC", "minValue": 0, "maxValue": 1,
     "description": "LLM-as-judge: every claim supported by the cited filing extracts. Sampled on production; full on experiment runs."},
    {"name": "citation_coverage", "dataType": "NUMERIC", "minValue": 0, "maxValue": 1,
     "description": "LLM-as-judge: share of claims that carry a correct citation."},
    {"name": "analyst_feedback", "dataType": "CATEGORICAL",
     "categories": [{"label": "up", "value": 1}, {"label": "down", "value": 0}],
     "description": "Analyst thumbs on an answer. Down-votes carry the analyst's comment and feed certification-review intake."},
]

# The certification-review queue binds the SAME score configs certification uses —
# annotation is not a pass/fail verdict on results; the reviewer scores the criteria
# themselves (groundedness, citation coverage, the deterministic scales), and those
# human scores ARE the ground truth the suite inherits.
REVIEW_QUEUE_CONFIGS = ["groundedness", "citation_coverage", "numeric_accuracy",
                        "citation_format", "escalation_correctness"]
# kept for older imports
INTAKE_QUEUE_CONFIGS = SIGNOFF_QUEUE_CONFIGS = REVIEW_QUEUE_CONFIGS

_NUMERIC_KINDS = ("figure", "trend", "dscr", "covenant", "leverage")
_OOS_KINDS = ("unanswerable", "advice", "speculation", "pii", "escalation")


def _skewed(rng: Rng, mu: float, lo: float = 0.45) -> float:
    """Skewed-realistic quality draw: most mass near the top, a visible left tail."""
    v = mu + rng.gauss(0, 0.045)
    if rng.chance(0.07):
        v -= rng.uniform(0.1, 0.35)
    return round(min(0.99, max(lo, v)), 3)


def deterministic_scores(rng: Rng, spec, scoring) -> list[dict]:
    """Assertion scores on a production trace (kind-aware, sampled per config)."""
    events = []
    s = rng.sub("detscore", spec.trace_id)
    if spec.error_step == "generation":
        return []  # failed generations carry no answer to assert on
    if s.chance(scoring.citation_format_coverage):
        ok = spec.answer.answer_type != "factual" or bool(spec.answer.citations)
        ok = ok and not s.chance(0.01)
        events.append(score_event(
            score_id=s.score_id("citfmt", spec.trace_id), name="citation_format",
            value="pass" if ok else "fail", data_type="CATEGORICAL",
            timestamp=spec.timestamp, trace_id=spec.trace_id, environment=spec.environment,
            comment=None if ok else "answer carries no machine-readable citations"))
    if spec.question_kind in _NUMERIC_KINDS and s.chance(scoring.numeric_check_ratio):
        bad = spec.error_mode in ("sign", "units")
        detail = None
        if bad:
            wrong = next(iter(spec.answer.figures.values()), 0)
            detail = f"figure {wrong:,} does not match the printed table value"
        events.append(score_event(
            score_id=s.score_id("numacc", spec.trace_id), name="numeric_accuracy",
            value="fail" if bad else "pass", data_type="CATEGORICAL",
            timestamp=spec.timestamp, trace_id=spec.trace_id, environment=spec.environment,
            comment=detail))
    if spec.question_kind in _OOS_KINDS and s.chance(scoring.escalation_check_coverage):
        ok = spec.answer.answer_type in ("declined", "abstained", "escalated")
        events.append(score_event(
            score_id=s.score_id("esc", spec.trace_id), name="escalation_correctness",
            value="pass" if ok else "fail", data_type="CATEGORICAL",
            timestamp=spec.timestamp, trace_id=spec.trace_id, environment=spec.environment))
    return events


def judge_scores(rng: Rng, spec, scoring, *, dip: float = 0.0,
                 force: bool = False) -> list[dict]:
    """LLM-judge samples on production traces. ``dip`` lowers the groundedness mean
    inside the v6 prompt era (the optional ambience, spec v2 §8)."""
    events = []
    if spec.error_step == "generation":
        return []
    gs = rng.sub("gscore", spec.trace_id)
    if force or gs.chance(scoring.groundedness_judge_ratio):
        mu = 0.91 - dip
        if spec.error_mode in ("sign", "units"):
            mu = 0.45  # the judge notices the figure contradicting the extract
        events.append(score_event(
            score_id=gs.score_id("ground", spec.trace_id), name="groundedness",
            value=_skewed(gs, mu, lo=0.3), data_type="NUMERIC", timestamp=spec.timestamp,
            trace_id=spec.trace_id, observation_id=spec.answer_obs_id,
            environment=spec.environment))
    cs = rng.sub("cscore", spec.trace_id)
    if force or cs.chance(scoring.citation_judge_ratio):
        mu = 0.93
        if spec.error_mode == "miscite" or (spec.answer.answer_type == "factual"
                                            and not spec.answer.citations):
            mu = 0.35  # fluent but unsourced — what a human skim would miss
        events.append(score_event(
            score_id=cs.score_id("citcov", spec.trace_id), name="citation_coverage",
            value=_skewed(cs, mu, lo=0.2), data_type="NUMERIC", timestamp=spec.timestamp,
            trace_id=spec.trace_id, environment=spec.environment))
    return events


def analyst_feedback_score(rng: Rng, trace_id: str, ts: datetime, environment: str,
                           response_ratio: float, down_rate: float,
                           force: bool = False, force_down: bool = False,
                           comment: str | None = None) -> tuple[list[dict], bool]:
    """User feedback — sparse thumbs. Returns ``(events, down)``."""
    s = rng.sub("fbscore", trace_id)
    if not force and not s.chance(response_ratio):
        return [], False
    down = True if force_down else s.chance(down_rate)
    ev = score_event(score_id=s.score_id("feedback", trace_id), name="analyst_feedback",
                     value="down" if down else "up", data_type="CATEGORICAL", timestamp=ts,
                     trace_id=trace_id, environment=environment,
                     comment=comment if down else None)
    return [ev], down


_HUMAN_NOTE = "human annotation (certification-review)"


def human_annotation_scores(rng: Rng, spec, *, ground_truth_note: str = "",
                            wrong_numeric: bool = False) -> list[dict]:
    """The certification-review queue's output: the reviewer scores the SAME criteria
    certification uses — these human scores are the ground truth the suite inherits.
    Kind-aware like the automated checks; comments mark the human provenance (seeded
    scores can't carry source=ANNOTATION — a documented cosmetic)."""
    s = rng.sub("human", spec.trace_id)
    ts = spec.timestamp + timedelta(minutes=s.randint(30, 200))
    tid, env = spec.trace_id, spec.environment
    note = f"{_HUMAN_NOTE}" + (f" — {ground_truth_note}" if ground_truth_note else "")
    events: list[dict] = []

    def cat(name, value):
        events.append(score_event(score_id=s.score_id(f"h_{name}", tid), name=name,
                                  value=value, data_type="CATEGORICAL", timestamp=ts,
                                  trace_id=tid, environment=env, comment=note))

    def num(name, value):
        events.append(score_event(score_id=s.score_id(f"h_{name}", tid), name=name,
                                  value=value, data_type="NUMERIC", timestamp=ts,
                                  trace_id=tid, environment=env, comment=note))

    num("groundedness", 0.25 if wrong_numeric else _skewed(s, 0.93))
    num("citation_coverage", _skewed(s, 0.94))
    if spec.question_kind in _NUMERIC_KINDS:
        cat("numeric_accuracy", "fail" if wrong_numeric else "pass")
    cat("citation_format", "pass")
    if spec.question_kind in _OOS_KINDS:
        cat("escalation_correctness",
            "pass" if spec.answer.answer_type in ("declined", "abstained", "escalated")
            else "fail")
    return events


def human_judge_pair(rng: Rng, spec, scoring) -> list[dict]:
    """On queue-completed traces both human AND judge scores exist on the same
    criteria scales — correlated but not identical (agreement ~88%, visible
    disagreements; the judge's comment names the disagreement)."""
    s = rng.sub("agree", spec.trace_id)
    agree = s.chance(scoring.judge_human_agreement)
    events = human_annotation_scores(rng, spec)
    judge = judge_scores(rng, spec, scoring, force=True)
    if not agree and judge:
        body = judge[0]["body"]
        v = float(body["value"])
        body["value"] = round(max(0.2, min(0.99, (1.2 - v))), 3)
        body["comment"] = "judge disagrees with the human annotation on this trace"
    return events + judge
