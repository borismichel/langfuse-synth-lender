"""Inspect and retire only kit-owned predecessors through the stable rule API.

A successor must have the intended filters and stable evaluator assignment before
its predecessor is disabled. No evaluator, rule or historical score is deleted.
Legacy trace/dataset rules cannot be re-enabled via the stable API; retaining their
configuration keeps an audit/rollback reference without promising a one-PATCH undo.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from .judges import (
    CODE_EVALUATORS, JUDGE_TEMPLATES, certification_dataset_ids, list_judges,
    get_rule, list_rules, patch_rule, predecessor_names, rule_filter, rule_name, successor_assignment,
)
from .reads import probe_reader

LIVE_TARGETS = ("observation", "experiment")
OBSERVATION_SOURCES = ("input", "output", "metadata", "tool_calls")


@dataclass
class Inventory:
    api_available: bool = True
    error: str = ""
    successors: list[dict] = field(default_factory=list)
    legacy: list[dict] = field(default_factory=list)
    evaluators: dict[str, dict] = field(default_factory=dict)

    def successor(self, judge: str, target: str = "observation") -> dict | None:
        matches = [r for r in self.successors if r.get("name") == rule_name(judge, target)]
        return matches[0] if len(matches) == 1 else None


def inventory(cfg: Config) -> Inventory:
    rules, available, err = list_rules(cfg.target.base_url)
    inv = Inventory(api_available=available, error=err)
    if not available or err:
        return inv
    evaluators, available, err = list_judges(cfg.target.base_url)
    inv.api_available, inv.error = available, err
    if not available or err:
        return inv
    for name in (*CODE_EVALUATORS, *JUDGE_TEMPLATES):
        for target in LIVE_TARGETS:
            successors = [r for r in rules if r.get("name") == rule_name(name, target)]
            predecessors = [r for r in rules if r.get("name") in predecessor_names(name, target)]
            if len(successors) > 1 or len(predecessors) > 1:
                inv.error = f"ambiguous rules for {name} ({target}); resolve duplicate names before retirement"
                return inv
        matches = [e for e in evaluators if e.get("name") == name]
        if len(matches) != 1:
            continue  # ambiguous evaluator identity cannot authorize retirement
        ev = matches[0]
        inv.evaluators[name] = ev
        for rule in rules:
            # A shared rule belongs to its operator. Retiring it would also stop
            # unrelated assignments, even when the deployment name is ours.
            if {a.get("evaluatorId") for a in rule.get("evaluatorAssignments", [])} != {ev["id"]}:
                continue
            for target in LIVE_TARGETS:
                if rule.get("name") == rule_name(name, target):
                    inv.successors.append(rule)
                elif rule.get("name") in predecessor_names(name, target):
                    inv.legacy.append(rule)
    return inv


def retire_legacy(cfg: Config, inv: Inventory | None = None, *,
                  dataset_ids: list[str] | None = None) -> tuple[list[str], list[str]]:
    inv = inv or inventory(cfg)
    if inv.error or not inv.api_available:
        return [], [f"could not read this project's evaluation rules ({inv.error or 'stable API unavailable'}) — nothing retired"]
    if dataset_ids is None:
        dataset_ids, _ = certification_dataset_ids(cfg)
    retired, notes = [], []
    for rule in inv.legacy:
        if not rule.get("enabled"):
            continue
        name = rule["name"]
        evaluator_id = rule["evaluatorAssignments"][0]["evaluatorId"]
        judge = next(n for n, e in inv.evaluators.items() if e["id"] == evaluator_id)
        target = "experiment" if name in predecessor_names(judge, "experiment") else "observation"
        successor = inv.successor(judge, target)
        if successor:
            successor, read_error = get_rule(cfg, successor["id"])
            if read_error:
                notes.append(f"{name}: left ENABLED — {read_error}")
                continue
        assignment, mapping_error = successor_assignment(inv.evaluators[judge], rule)
        valid = (successor is not None and not mapping_error
                 and (target != "experiment" or bool(dataset_ids))
                 and successor.get("filter") == rule_filter(target, dataset_ids)
                 and successor.get("enabled") == rule.get("enabled")
                 and successor.get("sampling") == rule.get("sampling")
                 and successor.get("evaluatorAssignments") == [assignment])
        # Duplicate predecessors are also ambiguous even if a successor exists.
        peers = [r for r in inv.legacy if r.get("name") in predecessor_names(judge, target)]
        if not valid or len(peers) != 1:
            notes.append(f"{name}: left ENABLED — replacement configuration/assignment is not validated")
            continue
        ok, err = patch_rule(cfg, rule["id"], enabled=False)
        if ok:
            retired.append(name)
        else:
            notes.append(f"{name}: {err}")
    return retired, notes


@dataclass
class Comparison:
    """The successor's scores on newly ingested data, beside the legacy rule's."""

    judge: str
    successor_scores: int = 0
    legacy_scores: int = 0
    #: Traces where both rules scored — the only ones a comparison can be made on.
    compared: int = 0
    agreed: int = 0
    disagreed: list[tuple[str, object, object]] = field(default_factory=list)
    summary: str = ""

    @property
    def ready(self) -> bool:
        """True when the successor may be enabled.

        It has to have scored something — a successor that produced nothing has not been
        validated, whatever the legacy rule did. Where a legacy baseline exists the two
        must agree on a majority of the traces both scored; where none exists (the shipped
        configs create the live rule paused, so this is the normal case) there is nothing
        to disagree with, and that is said rather than silently counted as agreement."""
        if self.successor_scores == 0:
            return False
        return self.compared == 0 or self.agreed * 2 > self.compared


def _reader(cfg: Config):
    """The read seam for this target (its own function so a test can stand in for it)."""
    return probe_reader(cfg.target.base_url)


def _same(a, b, tolerance: float) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tolerance
    return a == b


def compare(cfg: Config, judge: str, *, tolerance: float = 0.05) -> Comparison:
    """Compare the successor's scores with the legacy rule's, for one judge.

    Both rules write the same score *name* — the evaluator's — so what separates them is
    where the score landed: the successor scores the trace's **root** observation, the
    legacy rule scored a generation inside it. That is the discriminator used here, and it
    is why the successor had to be given a distinct target rather than a re-pointed one."""
    cmp = Comparison(judge=judge)
    reader = _reader(cfg)
    scores = [s for s in reader.scores(name=judge) if s.observation_id and s.trace_id]
    if not scores:
        cmp.summary = f"{judge}: no successor scores yet — enable it for a validation " \
                      "window against newly ingested traffic first"
        return cmp

    roots: dict[str, set[str]] = {}
    for trace_id in {s.trace_id for s in scores}:
        roots[trace_id] = {o.id for o in reader.observations(trace_id=trace_id) if o.is_root}

    by_trace: dict[str, dict[str, object]] = {}
    for s in scores:
        side = "successor" if s.observation_id in roots.get(s.trace_id, ()) else "legacy"
        by_trace.setdefault(s.trace_id, {})[side] = s.value
    cmp.successor_scores = sum(1 for v in by_trace.values() if "successor" in v)
    cmp.legacy_scores = sum(1 for v in by_trace.values() if "legacy" in v)

    for trace_id, sides in sorted(by_trace.items()):
        if "successor" not in sides or "legacy" not in sides:
            continue
        cmp.compared += 1
        if _same(sides["successor"], sides["legacy"], tolerance):
            cmp.agreed += 1
        else:
            cmp.disagreed.append((trace_id, sides["successor"], sides["legacy"]))

    if cmp.successor_scores == 0:
        cmp.summary = (f"{judge}: no successor scores on {cmp.legacy_scores} trace(s) the "
                       "legacy rule scored — enable it for a validation window first")
    elif cmp.compared == 0:
        cmp.summary = (f"{judge}: {cmp.successor_scores} successor score(s), no legacy "
                       "baseline to compare against (the legacy rule never ran here)")
    else:
        cmp.summary = (f"{judge}: {cmp.agreed}/{cmp.compared} agree within ±{tolerance}"
                       + (f"; disagreements on {[d[0] for d in cmp.disagreed]}"
                          if cmp.disagreed else ""))
    return cmp


def enable_successors(cfg: Config, *, sampling: float, tolerance: float = 0.05,
                      inv: Inventory | None = None) -> tuple[list[str], list[str]]:
    """Enable each validated observation successor at ``sampling``.

    Returns ``(enabled names, notes)``. A successor whose comparison is not ready is left
    disabled and the reason is returned — this function never enables on a judgement call
    the operator has not seen."""
    inv = inv or inventory(cfg)
    if inv.error:
        return [], [f"could not read this project's evaluation rules ({inv.error}) — "
                    "nothing enabled"]
    if not inv.api_available:
        return [], ["stable evaluator API not available — enable the rule in the UI"]
    enabled, notes = [], []
    for judge in JUDGE_TEMPLATES:
        rule = inv.successor(judge)
        if rule is None:
            notes.append(f"{judge}: no observation successor in this project — "
                         "run `synth evaluators` first")
            continue
        cmp = compare(cfg, judge, tolerance=tolerance)
        if not cmp.ready:
            notes.append(cmp.summary)
            continue
        ok, err = patch_rule(cfg, rule["id"], enabled=True, sampling=sampling)
        if ok:
            enabled.append(rule["name"])
        else:
            notes.append(f"{rule['name']}: {err[:90]}")
    return enabled, notes
