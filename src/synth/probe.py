"""`synth probe` — verify backdated ingestion EARLY (spec v2 §3).

Ingests one fully-formed trace with an explicit historical timestamp (3 days ago),
polls it back via the public API, and compares timestamps. If the host dropped or
normalised the backdate (e.g. a Cloud tier behaving differently from self-hosted),
this fails loudly BEFORE any bulk generation. The probe trace is deterministic
(id keyed off the seed) and tagged ``synth-probe`` so it is easy to spot and ignore.
"""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Callable

import requests

from .config import Config
from .seed.ingest import Ingestor, assert_demo_project
from .timegen import now_utc


def run_probe(cfg: Config, log: Callable[[str], None] = print) -> bool:
    base = cfg.target.base_url
    _pid, project_name = assert_demo_project(base, cfg.target.project_hint)
    log(f"✓ guardrail passed: project {project_name!r}")

    from .agent import answer_deterministic
    from .content import flagged_cases
    from .rng import Rng
    from .seed.traces import TraceSpec, build_trace_events

    rng = Rng(cfg.generation.seed)
    backdate = now_utc() - timedelta(days=3, hours=2)
    case = flagged_cases(rng)[0]
    spec = TraceSpec(
        trace_id=rng.trace_id("probe", "backdate-check"), timestamp=backdate,
        question=case.question, answer=answer_deterministic(case.question),
        user_id="synth_probe", session_id=None, environment="staging", kind="probe",
        question_kind="figure", prompt_version=cfg.certification.production_version,
        tags=["synth-probe"])
    events = build_trace_events(rng, cfg, spec)
    ing = Ingestor.from_env(base)
    ing.extend(events)
    ing.flush()
    log(f"· probe trace {spec.trace_id[:16]}… ingested with timestamp {backdate.isoformat()}")

    # poll the read API (ingestion is async; allow it a moment)
    pub_auth = ing.public_key, ing.secret_key
    got = None
    for attempt in range(10):
        time.sleep(2 + attempt)
        resp = requests.get(f"{base}/api/public/traces/{spec.trace_id}",
                            auth=pub_auth, timeout=20)
        if resp.status_code == 200:
            got = resp.json()
            break
    if got is None:
        log("✗ PROBE FAILED: trace not retrievable after ~60s — check keys/host/ingestion.")
        return False

    stored = (got.get("timestamp") or "").replace("Z", "+00:00")
    want_date = backdate.strftime("%Y-%m-%dT%H:%M")
    ok = stored.startswith(want_date)
    if ok:
        n_obs = len(got.get("observations") or [])
        log(f"✓ PROBE PASSED: stored timestamp {stored} matches the backdate; "
            f"{n_obs} observations attached. Backdated bulk seeding is safe on this host.")
    else:
        log(f"✗ PROBE FAILED: sent {backdate.isoformat()} but the host stored {stored!r} — "
            "backdating is dropped or normalised here. DO NOT bulk-seed; the 30-day "
            "window would collapse onto today.")
    return ok
