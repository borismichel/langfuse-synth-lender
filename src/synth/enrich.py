"""`synth enrich` — the optional archetype layer (spec v2 §4, tier 2).

~50 cheap-model generations of analyst-copilot answer phrasings, mined into
templates and written to ``fixtures/archetypes.json``. When that file exists, the
content layer varies ambient **prose** with these phrasings (graded fields — figures,
ratios, citations, answer_type — are never touched, so grading and determinism are
unaffected; the seed itself remains model-free and reproducible for a given
archetype file, which is committed once generated).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .config import Config
from .state import REPO_ROOT

ARCHETYPES_PATH = REPO_ROOT / "fixtures" / "archetypes.json"

_PROMPTS = {
    "figure": ("Write {n} short single-sentence variants for a financial analyst copilot "
               "reporting one extracted figure. Use the placeholders {{borrower}}, "
               "{{label}}, {{value}} and mention the cited extract. One per line, no numbering."),
    "summary": ("Write {n} short two-sentence variants for a financial analyst copilot "
                "summarising the credit picture from filed statements. Use the placeholders "
                "{{borrower}} and {{detail}}. One per line, no numbering."),
    "declined": ("Write {n} short single-sentence variants of a copilot politely declining "
                 "to give a credit recommendation, offering figure verification instead. "
                 "One per line, no numbering."),
}


def run_enrich(cfg: Config, n: int = 50, log: Callable[[str], None] = print) -> Path:
    from .lfclient import get_anthropic

    anth = get_anthropic()
    model = cfg.certification.candidate_b_model  # the cheap tier
    per_kind = max(3, n // len(_PROMPTS))
    out: dict[str, list[str]] = {}
    for kind, prompt in _PROMPTS.items():
        log(f"· generating {per_kind} {kind!r} phrasings with {model} …")
        resp = anth.messages.create(
            model=model, max_tokens=1500, temperature=0.9,
            messages=[{"role": "user", "content": prompt.format(n=per_kind)}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        lines = [ln.strip("-• ").strip() for ln in text.splitlines() if ln.strip()]
        keep = [ln for ln in lines if "{" in ln or kind == "declined"]
        out[kind] = keep[:per_kind]
        log(f"  kept {len(out[kind])} usable templates")
    ARCHETYPES_PATH.parent.mkdir(exist_ok=True)
    ARCHETYPES_PATH.write_text(json.dumps(out, indent=2))
    return ARCHETYPES_PATH
