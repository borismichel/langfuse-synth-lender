"""Moving this kit's managed evaluators onto their v4 observation-scoped successors.

Evaluation rules are **project state**: they live in Langfuse, not in this repo, and a
`seed` against a project that has been seeded before meets whatever the last version of
this kit left there. That is what makes the v4 evaluator migration different from the rest
of Spec H — the write path and the read seam changed code, this changes rows in someone
else's database — and it is why the migration is a lifecycle rather than an edit:

1. **provision** the successor, always disabled (``judges.ensure_rule``, called by
   ``seed.run._populate_managed_evaluators`` — i.e. by `seed` and by `synth evaluators`);
2. **retire** the predecessor by disabling it, never deleting it (:func:`retire_legacy`,
   called from the same place);
3. **validate** the successor on newly ingested data and compare its scores against the
   legacy rule's (:func:`compare`);
4. **enable** it, at the configured sampling, only once step 3 is satisfied
   (:func:`enable_successors`).

Steps 3 and 4 are an operator's, behind `synth evaluators --enable-live`, because they are
the two that change what a demo project *scores* — and because the shipped configs create
the live rule paused (``certification.trace_judge_sampling`` defaults to 0.0), so the
ordinary depot deployment never needs them.

**What "successor" means concretely.** The pre-v4 live rule matched ``type = GENERATION``.
Under v4 that matches the planning generation and the answer generation of every copilot
turn — two scores per trace, one of them grading tool-call JSON. The successor targets the
turn's **root observation** instead: one per trace, carrying the analyst's question as its
input and the copilot's answer as its output. Every variable the judge reads is already
there, which is the consolidation v4 requires — an observation evaluator cannot read
siblings or children.

The certification rules do not move. They were already ``target=experiment``, which is v4's
successor to the legacy ``dataset`` target, and the code evaluators grade against
``expected_output`` — a source only the experiment target exposes.

The unstable-API risk this rides on is recorded in :mod:`synth.workbench.judges`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from .judges import JUDGE_TEMPLATES, list_rules, patch_rule, rule_name
from .reads import probe_reader

#: v4's whole evaluation-rule target vocabulary. ``trace`` and ``dataset`` are the legacy
#: targets the unstable API still returns for existing rows but no longer accepts.
LIVE_TARGETS = ("observation", "experiment")

#: Legacy targets — a rule on one of these is a migration subject by definition.
RETIRED_TARGETS = ("trace", "dataset")

#: Variable sources an ``observation`` rule may map from. Anything else (notably
#: ``expected_output``) is experiment-only.
OBSERVATION_SOURCES = ("input", "output", "metadata", "tool_calls")

#: The suffix this kit's live rule carried before v4 (``wb-<judge>-traces``). Kept so
#: :func:`retire_legacy` can recognise its own predecessor by name and leave every other
#: project's rules alone.
_LEGACY_LIVE_SUFFIX = "-traces"


def _ours(rule: dict) -> bool:
    """True for a rule this kit created — under the current naming or the pre-v4 one.

    A rule is ours when its *deployment* name is the one we would give a rule for its
    evaluator; two rules can share an evaluator and only one of them be ours."""
    name = rule.get("name") or ""
    judge = (rule.get("evaluator") or {}).get("name")
    if not judge:
        return False
    return (name == f"wb-{judge}{_LEGACY_LIVE_SUFFIX}"
            or any(name == rule_name(judge, t) for t in LIVE_TARGETS))


@dataclass
class Inventory:
    """What the project currently holds, split by what the migration must do with it."""

    api_available: bool = True
    #: Rules already on a v4 target and named by this kit's current scheme.
    successors: list[dict] = field(default_factory=list)
    #: Rules to retire: a legacy target, or this kit's pre-v4 live rule.
    legacy: list[dict] = field(default_factory=list)

    def successor(self, judge: str, target: str = "observation") -> dict | None:
        want = rule_name(judge, target)
        return next((r for r in self.successors if r.get("name") == want), None)


def inventory(cfg: Config) -> Inventory:
    """Read every evaluation rule in the project and classify it.

    Two different things land in :attr:`Inventory.legacy`, and the difference matters:

    * a rule on a **retired target** (``trace`` / ``dataset``), whoever created it. Under
      v4 it stops producing results at the Cloud cutover, so disabling it is the
      documented migration step rather than a liberty taken with someone else's rule;
    * this kit's own **pre-v4 live rule** (``wb-<judge>-traces``), recognised by name.

    Anything else — a rule on a v4 target that this kit did not create — is left out of
    both lists on purpose: switching off someone else's evaluation is not this migration's
    business."""
    rules, available = list_rules(cfg.target.base_url)
    inv = Inventory(api_available=available)
    if not available:
        return inv
    for rule in rules:
        if rule.get("target") in RETIRED_TARGETS:
            inv.legacy.append(rule)
        elif not _ours(rule):
            continue
        elif (rule.get("name") or "").endswith(_LEGACY_LIVE_SUFFIX):
            inv.legacy.append(rule)
        else:
            inv.successors.append(rule)
    return inv


def retire_legacy(cfg: Config, inv: Inventory | None = None) -> tuple[list[str], list[str]]:
    """Disable every legacy rule whose successor is in place. Returns ``(names, notes)``.

    Disabled, never deleted: the row keeps its filters, mappings and history, so a rollback
    is one PATCH back to ``enabled=true`` and the scores it already wrote stay readable.

    **A predecessor is only retired once its successor exists.** Everything in
    :mod:`.judges` degrades to a logged note rather than failing a `seed` — which is right
    for a capability that may be absent, and wrong if it lets a retirement outlive the
    creation it was paired with. A rejected filter or an absent LLM connection would
    otherwise leave the project with the old rule off and no new one on: judging silently
    stops, and the log line that said so scrolled past during a seed. A rule on a
    **retired target** is exempt from that check — it stops producing results at the v4
    cutover whatever we do, so leaving it enabled buys nothing."""
    inv = inv or inventory(cfg)
    if not inv.api_available:
        return [], ["unstable evaluator API not available — retire the legacy rule in the UI"]
    retired, notes = [], []
    for rule in inv.legacy:
        if rule.get("enabled") is False:
            continue                                   # already retired; nothing to do
        name = rule.get("name") or rule["id"]
        judge = (rule.get("evaluator") or {}).get("name")
        if rule.get("target") not in RETIRED_TARGETS and not inv.successor(judge or ""):
            notes.append(f"{name}: left ENABLED — its observation successor is not in this "
                         "project, so retiring it would stop judging with nothing to take over")
            continue
        ok, err = patch_rule(cfg, rule["id"], enabled=False)
        if ok:
            retired.append(name)
        else:
            notes.append(f"{name}: {err[:90]}")
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
    if not inv.api_available:
        return [], ["unstable evaluator API not available — enable the rule in the UI"]
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
