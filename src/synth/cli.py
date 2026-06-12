"""`synth` CLI (spec §11):

    synth plan          --config demo.yaml   # dry-run: volumes, suites, seeded-run summary
    synth seed          --config demo.yaml   # generate + ingest backdated; prompt; suites; runs; queues
    synth import-spool                       # resume an interrupted upload
    synth verify        --config demo.yaml   # query back via the API, assert the golden path
    synth certify       --config demo.yaml --model <id>   # the button (live model calls)
    synth memo          --config demo.yaml   # render CERT_MEMO.md (the validation dossier)
    synth script        --config demo.yaml   # (re)generate DEMO_SCRIPT.md from run state
    synth submit / playground                # the live analyst-copilot surface
"""
from __future__ import annotations

import json

import typer
from dotenv import load_dotenv

from .config import load_config
from .state import RunState

app = typer.Typer(add_completion=False,
                  help="Langfuse demo-data synthesiser — regulated-lender model-certification scenario.")

DEFAULT_CONFIG = "config/demo.yaml"


def _load(config: str):
    load_dotenv()  # pick up .env
    return load_config(config)


@app.command()
def plan(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c")):
    """Dry run: print volumes, suites, flagged cases and the seeded-run summary. No network."""
    from .seed.run import run_seed

    cfg = _load(config)
    state = run_seed(cfg, dry_run=True, persist=False, log=lambda m: typer.echo(m))
    typer.echo("\n— PLAN SUMMARY —")
    typer.echo(json.dumps(state.summary, indent=2))


@app.command()
def seed(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
         dry_run: bool = typer.Option(False, "--dry-run", help="Build everything but send nothing."),
         spool: str = typer.Option(None, "--spool", help="NDJSON spool path (default .synth_spool/events.ndjson)."),
         no_import: bool = typer.Option(False, "--no-import",
                                        help="Write the spool to disk but skip the upload (resume with `synth import-spool`).")):
    """Generate the deterministic caseload + certification record, spool to disk, batch-import
    backdated, register the pinned prompt, create the suites + seeded runs + annotation
    queues, and emit DEMO_SCRIPT.md."""
    from .script import render_script
    from .seed.run import run_seed

    cfg = _load(config)
    state = run_seed(cfg, dry_run=dry_run, spool_path=spool, do_import=not no_import,
                     log=lambda m: typer.echo(m))
    out = render_script(cfg, state)
    typer.echo(f"✓ DEMO_SCRIPT.md written -> {out}")


@app.command(name="import-spool")
def import_spool(spool: str = typer.Argument(None, help="Spool file to import (default .synth_spool/events.ndjson)."),
                 config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c")):
    """Resume an interrupted upload: batch-import an existing NDJSON spool without regenerating."""
    from .seed.run import import_spool_file

    cfg = _load(config)
    import_spool_file(cfg, spool, log=lambda m: typer.echo(m))


@app.command()
def verify(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c")):
    """Query the data back via the API and assert the golden path."""
    from .verify import run_verify

    cfg = _load(config)
    if not RunState.exists():
        typer.echo("No .synth_state.json — run `synth seed` first.", err=True)
        raise typer.Exit(code=2)
    state = RunState.load()
    report = run_verify(cfg, state, log=lambda m: typer.echo(m))
    typer.echo(f"\n{'✓ ALL CHECKS PASSED' if report.ok else '✗ SOME CHECKS FAILED'}")
    raise typer.Exit(code=0 if report.ok else 1)


@app.command()
def certify(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
            model: str = typer.Option(None, "--model", "-m",
                help="Model to certify (default: certification.candidate_a_model)."),
            gate: bool = typer.Option(False, "--gate",
                help="Apply the per-scenario thresholds; exit non-zero on failure."),
            offline: bool = typer.Option(False, "--offline",
                help="No-model CI smoke: assert the suite is self-consistent.")):
    """Run a model against the certification-suite live (real model calls, temperature 0).
    Deterministic assertions score as code evaluators; the managed judges score the run
    in the UI. The seeded baseline/candidate runs were produced by the same pipeline."""
    from .certify.run import apply_gate, certify as _certify, offline_check

    cfg = _load(config)
    if offline:
        ok = offline_check(cfg, log=lambda m: typer.echo(m))
        raise typer.Exit(code=0 if ok else 1)

    model = model or cfg.certification.candidate_a_model
    result = _certify(cfg, model, log=lambda m: typer.echo(m))
    if gate:
        ok = apply_gate(cfg, result, log=lambda m: typer.echo(m))
        raise typer.Exit(code=0 if ok else 1)


@app.command()
def probe(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c")):
    """Verify EARLY that backdated ingestion behaves on this host (spec v2 §3):
    ingest ONE trace with a historical timestamp, query it back, and FAIL LOUDLY if
    the timestamp was dropped or normalised. Run before any bulk seed on Cloud."""
    from .probe import run_probe

    cfg = _load(config)
    ok = run_probe(cfg, log=lambda m: typer.echo(m))
    raise typer.Exit(code=0 if ok else 1)


@app.command()
def enrich(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
           n: int = typer.Option(50, "--n", help="Archetype generations (cheap model).")):
    """OPTIONAL archetype layer (spec v2 §4): ~50 cheap-model generations of answer
    phrasings, mined into templates (fixtures/archetypes.json) that vary ambient
    prose. Needs ANTHROPIC_API_KEY; the seed works fine without it."""
    from .enrich import run_enrich

    cfg = _load(config)
    out = run_enrich(cfg, n=n, log=lambda m: typer.echo(m))
    typer.echo(f"✓ archetypes written -> {out}")


@app.command()
def memo(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c")):
    """Render CERT_MEMO.md — the model-validation dossier — from run state."""
    from .memo import render_memo

    cfg = _load(config)
    if not RunState.exists():
        typer.echo("No .synth_state.json — run `synth seed` first.", err=True)
        raise typer.Exit(code=2)
    out = render_memo(cfg, RunState.load())
    typer.echo(f"✓ CERT_MEMO.md written -> {out}")


@app.command()
def script(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c")):
    """(Re)generate DEMO_SCRIPT.md from the current run state."""
    from .script import render_script

    cfg = _load(config)
    if not RunState.exists():
        typer.echo("No .synth_state.json — run `synth seed` first.", err=True)
        raise typer.Exit(code=2)
    out = render_script(cfg, RunState.load())
    typer.echo(f"✓ DEMO_SCRIPT.md written -> {out}")


@app.command()
def submit(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
           prefab: str = typer.Option("paren", "--prefab",
               help="One of: paren, units, covenant, unanswerable, advice, escalation."),
           model: str = typer.Option(None, "--model", "-m",
               help="Model to ask (default: the incumbent).")):
    """Ask one live copilot question from the terminal and emit its trace."""
    from .live.prefabs import prefabs_by_key
    from .live.submit import submit as _submit

    cfg = _load(config)
    p = prefabs_by_key(cfg.generation.seed).get(prefab)
    if not p:
        typer.echo(f"unknown prefab {prefab!r}", err=True)
        raise typer.Exit(code=2)
    res = _submit(cfg, p.question, model, log=lambda m: typer.echo(m))
    a, e = res["answer"], res["expected"]
    typer.echo(f"\n— ANSWER ({res['model']}, prompt v{res['prompt_version']}) —")
    typer.echo(f"  {a.answer_type.upper()}  ·  {a.answer}")
    if a.figures:
        typer.echo(f"  figures: { {k: f'{v:,}' for k, v in a.figures.items()} }")
    typer.echo(f"  ground truth: {e.answer_type} · {e.figures or e.answer[:60]}")
    typer.echo(f"  trace → {res['trace_url']}")


@app.command()
def playground(config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
               host: str = typer.Option("127.0.0.1", "--host"),
               port: int = typer.Option(8000, "--port")):
    """Serve the analyst-copilot UI + /dossier (needs the `playground` extra:
    pip install -e '.[playground]')."""
    cfg = _load(config)
    try:
        import uvicorn
        from .live.app import create_app
    except ImportError:
        typer.echo("playground deps missing — run: pip install -e '.[playground]'", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"→ playground on http://{host}:{port}  (the pinned production prompt is pulled live per question)")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
