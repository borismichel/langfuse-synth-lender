"""Background experiment execution — the workbench's run engine.

Triggering a spec runs each target through the v4 ``run_experiment`` API with the
composed task (the release's model + pinned prompt, temperature 0 by default), the
selected deterministic evaluators from the code registry, and run metadata carrying
the **spec hash**, the **evaluator code SHAs**, and the optional **dataset freeze**
timestamp — so the run in Langfuse is self-describing evidence. Managed judges,
scoped to the datasets via evaluation rules, score the new runs automatically.

Execution happens on a background thread (single-flight); the UI polls a status dict.
Results are re-graded locally with the same shared grader (``grading.item_passes``)
and written to the results store, then merged into ``.synth_state.json`` so
``synth memo`` and the dossier pick the run up — exactly like ``synth certify``.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from ..config import Config
from ..grading import item_passes
from .links import Links
from .registry import discover_tasks, evaluator_by_name, fingerprints
from .results import ItemRow, WorkbenchRun, gate_verdicts, save_run
from .specs import ExperimentSpec

_LOCK = threading.Lock()
RUNS: dict[str, dict] = {}   # run_id -> {state, progress, total, message}


def status(run_id: str) -> dict:
    return RUNS.get(run_id, {})


def start_run(cfg: Config, spec: ExperimentSpec) -> tuple[str | None, str]:
    """Kick off a background run. Returns (run_id, error)."""
    with _LOCK:
        if any(s.get("state") == "running" for s in RUNS.values()):
            return None, "a run is already in progress (single-flight for the demo)"
        run_id = f"wb-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        RUNS[run_id] = {"state": "running", "progress": 0, "total": 0, "message": "starting"}
    t = threading.Thread(target=_execute, args=(cfg, spec, run_id), daemon=True)
    t.start()
    return run_id, ""


def _scenario_of(meta: dict) -> str:
    return (meta.get("scenario") or meta.get("slice") or "numeric_lookup")


def _make_task(lf, anth, release: dict):
    tasks = [t for t in discover_tasks() if not t.error]
    task_fn = tasks[0].fn if tasks else None

    def task(*args, **kwargs):
        item = kwargs.get("item") if "item" in kwargs else (args[0] if args else None)
        if task_fn is not None:
            return task_fn(item, model=release["model"], lf=lf, anth=anth,
                           prompt_name=release["prompt_name"])
        from ..agent import answer

        return answer(item.input, release["model"], live=True, lf=lf, anth=anth,
                      prompt_name=release["prompt_name"]).model_dump()

    return task


def _wrap_evaluators(names: list[str]):
    """Selected registry evaluators, bound to the SDK contract."""
    out = []
    for name in names:
        reg = evaluator_by_name(name)
        if reg is not None:
            out.append(reg.fn)
    return out


def _execute(cfg: Config, spec: ExperimentSpec, run_id: str) -> None:
    started = datetime.now(timezone.utc).isoformat()
    shas = fingerprints(spec.evaluators)
    run = WorkbenchRun(run_id=run_id, spec_ref=spec.ref, spec_hash=spec.spec_hash,
                       spec=spec.model_dump(), release=spec.release.model_dump(),
                       evaluator_shas=shas, started=started)
    try:
        from ..lfclient import get_anthropic, get_langfuse

        lf = get_langfuse(cfg)
        anth = get_anthropic()

        # resolve + pin the prompt version for the metadata record
        rel = spec.release
        prompt_kwargs = ({"version": rel.prompt_version} if rel.prompt_version
                         else {"label": rel.prompt_label})
        prompt = lf.get_prompt(rel.prompt_name, type="chat", cache_ttl_seconds=0, **prompt_kwargs)
        prompt_version = getattr(prompt, "version", rel.prompt_version)
        release = {**rel.model_dump(), "prompt_version": prompt_version}
        run.release = release

        task = _make_task(lf, anth, release)
        evaluators = _wrap_evaluators(spec.evaluators)
        links = Links.from_cfg(cfg)

        all_rows: list[ItemRow] = []
        for target in spec.targets:
            dataset = lf.get_dataset(target.dataset_name)
            dataset_id = getattr(dataset, "id", "") or ""
            items = list(dataset.items)
            if target.slices:
                items = [it for it in items
                         if ((getattr(it, "metadata", None) or {}).get("slice") in target.slices)]
            RUNS[run_id].update(total=RUNS[run_id]["total"] + len(items),
                                message=f"running {target.dataset_name} ({len(items)} items)")

            name = f"{spec.ref}-{release['model']}"
            res = lf.run_experiment(
                name=name,
                description=(f"Workbench spec {spec.ref} (hash {spec.spec_hash[:12]}…) — "
                             f"release {release['model']} + {rel.prompt_name} v{prompt_version}."),
                data=items,
                task=task,
                evaluators=evaluators,
                metadata={"spec_ref": spec.ref, "spec_hash": spec.spec_hash,
                          "evaluator_shas": shas, "release": release,
                          "frozen_dataset_version": spec.freeze_dataset_version,
                          "workbench": True},
            )
            run.langfuse_runs.append({"dataset": target.dataset_name, "run_name": name,
                                      "dataset_id": dataset_id,
                                      "runs_url": links.dataset_runs(dataset_id)})

            for ir in getattr(res, "item_results", []) or []:
                item = getattr(ir, "item", None)
                output = getattr(ir, "output", None)
                if item is None or output is None:
                    continue
                expected = getattr(item, "expected_output", None) or {}
                meta = getattr(item, "metadata", None) or {}
                ok, detail = item_passes(_scenario_of(meta), expected, output)
                scores = {}
                for ev in getattr(ir, "evaluations", None) or []:
                    scores[getattr(ev, "name", "?")] = {
                        "value": getattr(ev, "value", None),
                        "comment": getattr(ev, "comment", "") or ""}
                tid = getattr(ir, "trace_id", "") or ""
                all_rows.append(ItemRow(
                    dataset=target.dataset_name, item_id=getattr(item, "id", ""),
                    slice=meta.get("slice") or "(none)", passed=ok, scores=scores,
                    trace_id=tid,
                    trace_url=links.trace(tid),
                    detail="" if ok else detail))
                RUNS[run_id]["progress"] += 1
        lf.flush()

        from dataclasses import asdict

        run.rows = [asdict(r) for r in all_rows]
        run.gates = gate_verdicts(run.rows, spec.model_dump())
        run.state = "done"
        run.finished = datetime.now(timezone.utc).isoformat()
        _persist_to_state(cfg, spec, run, prompt_version)
        RUNS[run_id].update(state="done", message="finished")
    except Exception as exc:  # noqa: BLE001 — surface in UI, never a dead thread
        run.state = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished = datetime.now(timezone.utc).isoformat()
        RUNS[run_id].update(state="error", message=run.error)
    finally:
        save_run(cfg, run)


def _persist_to_state(cfg: Config, spec: ExperimentSpec, run: WorkbenchRun,
                      prompt_version) -> None:
    """Merge into .synth_state.json (same shape as certify._persist) so the memo and
    the dossier render workbench runs alongside CLI ones."""
    from ..state import RunState
    from ..timegen import iso_date, now_utc

    if not RunState.exists():
        return
    state = RunState.load()
    aggs: dict[str, dict] = {}
    for row in run.rows:
        a = aggs.setdefault(row["dataset"], {"n": 0, "passed": 0})
        a["n"] += 1
        a["passed"] += 1 if row["passed"] else 0
    for ds, a in aggs.items():
        suite_state = state.suites.setdefault("certification_suite", {"name": ds, "runs": {}})
        lf_run = next((r["run_name"] for r in run.langfuse_runs if r["dataset"] == ds),
                      run.run_id)
        suite_state.setdefault("runs", {})[lf_run] = {
            "model": run.release.get("model"), "verdict": "live",
            "pass_rate": round(a["passed"] / a["n"], 4) if a["n"] else 0.0,
            "prompt_version": prompt_version,
            "spec_hash": run.spec_hash,
            "date": iso_date(now_utc()),
        }
    state.save()
