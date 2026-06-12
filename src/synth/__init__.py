"""Langfuse demo-data synthesiser — regulated-lender model-certification scenario.

See the spec (``langfuse-demo-synth-spec.md``) and ``README.md``. The package is
organised as:

- ``config``        — typed load of ``config/demo.yaml``
- ``rng``           — single-seed deterministic RNG + W3C-format ID derivation
- ``models``        — ``AnalystQuestion`` / ``CopilotAnswer`` data contracts (spec §16)
- ``filings``       — the deterministic borrower/filing corpus
- ``agent``         — ``answer(question, model)`` (the one lever, spec §7)
- ``seed/*``        — backdated batch ingestion, traces, scores, prompt, suites,
                      seeded certification runs, annotation queues
- ``certify/*``     — the live certification runner (the button, spec §7) + gate
- ``memo``          — render ``CERT_MEMO.md`` (the validation dossier, spec §18)
- ``verify``        — query-back assertions for the golden path
- ``script``        — render ``DEMO_SCRIPT.md`` from run state (spec §18)
- ``cli``           — ``synth plan | seed | verify | certify | memo | script | playground``
"""

__version__ = "0.1.0"
