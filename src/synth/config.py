"""Typed configuration loaded from ``config/demo.yaml`` / ``config/cloud-demo.yaml``.

The full run is determined by ``(this config, generation.seed)``. Env vars supply
only secrets/URL (``LANGFUSE_*``, ``ANTHROPIC_API_KEY``); everything that affects
the *shape* of the generated data lives here so a run is auditable and reproducible.

Spec v2 (2026-06-12): volume is **sessions-driven** (sessions/day × log-normal turns;
total traces derived, not forced) with one ``scale`` parameter for the Cloud preset.
"""
from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class Target(BaseModel):
    host: str = "http://localhost:3000"
    project_hint: str = "demo"

    @property
    def base_url(self) -> str:
        # env wins so the same config can target different instances (Cloud or self-hosted)
        return os.environ.get("LANGFUSE_BASE_URL", self.host).rstrip("/")


class Volume(BaseModel):
    scale: float = 1.0                       # the one scaler (spec v2 §3)
    sessions_per_weekday: tuple[int, int] = (45, 55)
    sessions_per_weekend_day: tuple[int, int] = (3, 7)
    turns_median: float = 7.0                # log-normal: median ~7, p95 ~22
    turns_sigma: float = 0.7
    turns_max: int = 30


class Population(BaseModel):
    users: int = 48
    power_user_share: float = 0.1


class Environments(BaseModel):
    production_share: float = 0.96


class Generation(BaseModel):
    seed: int = 47
    archetype: str = "filing_copilot"
    window_days: int = 30
    tz_offset_hours: int = 2                 # Europe/Berlin business hours
    volume: Volume = Field(default_factory=Volume)
    population: Population = Field(default_factory=Population)
    environments: Environments = Field(default_factory=Environments)
    german_share: float = 0.2   # share of German-speaking analysts; consistent per user
                                 # AND per session (never mixes mid-chat). 0 = all English.


class Model(BaseModel):
    name: str
    role: Literal["work", "work2", "light"]
    input_per_1k: float
    output_per_1k: float


class ScenarioCfg(BaseModel):
    n_items: int
    gate: float = 0.95                       # threshold on the scenario's deterministic check


class DatasetCfg(BaseModel):
    name: str = "certification-suite"
    scenarios: dict[str, ScenarioCfg] = Field(default_factory=dict)

    @property
    def n_items(self) -> int:
        return sum(s.n_items for s in self.scenarios.values())


class QueueCfg(BaseModel):
    name: str = "certification-review"
    n_completed: int = 16
    n_pending: int = 14


class Certification(BaseModel):
    enabled: bool = True
    prompt_name: str = "analyst-copilot"
    n_prompt_versions: int = 8               # production = N-1, staging = N
    prompt_transition_day_offset: int = -12  # mid-window version transition (ambience hook)
    prompt_fix_day_offset: int = -8
    incumbent_model: str = "claude-sonnet-4-5"
    candidate_a_model: str = "claude-sonnet-4-6"
    candidate_b_model: str = "claude-haiku-4-5"
    judge_model: str = "claude-sonnet-4-6"
    # Live production-trace monitoring with the SAME judges (groundedness,
    # citation_coverage) via a target=observation evaluation rule. Sampling fraction on
    # live copilot generations. 0.0 = the rule is created but DEACTIVATED (visible as
    # configured-but-paused monitoring, zero judge triggers anywhere). Set to e.g. 0.05
    # to opt in to low-rate live judging of NEW traffic — evaluation rules never
    # backfill, so the backdated seed always triggers zero judge calls regardless.
    trace_judge_sampling: float = 0.0
    baseline_run_day_offset: int = -6
    candidate_run_day_offset: int = -1
    n_flagged_reserved: int = 1
    dataset: DatasetCfg = Field(default_factory=DatasetCfg)
    queue: QueueCfg = Field(default_factory=QueueCfg)

    @property
    def production_version(self) -> int:
        return max(1, self.n_prompt_versions - 1)

    @property
    def staging_version(self) -> int:
        return self.n_prompt_versions


class QualityDip(BaseModel):
    enabled: bool = True
    dip: float = 0.06


class NightlyBatch(BaseModel):
    enabled: bool = True
    traces_per_night: int = 2
    tag: str = "batch:covenant-monitor"


class Ambience(BaseModel):
    quality_dip: QualityDip = Field(default_factory=QualityDip)
    error_rate: float = 0.02
    nightly_batch: NightlyBatch = Field(default_factory=NightlyBatch)


class Scoring(BaseModel):
    # Every score-method type appears on the same scores surface (spec v2 §5):
    # deterministic assertions, LLM judges, human annotation, user feedback.
    citation_format_coverage: float = 1.0
    numeric_check_ratio: float = 0.35
    groundedness_judge_ratio: float = 0.12
    citation_judge_ratio: float = 0.12
    escalation_check_coverage: float = 1.0
    feedback_response_ratio: float = 0.11
    feedback_down_rate: float = 0.05
    judge_human_agreement: float = 0.88


class Workbench(BaseModel):
    brand: str = "Meridian Commercial Bank"
    results_dir: str = ".workbench"
    default_role: str = "builder"


class Config(BaseModel):
    target: Target = Field(default_factory=Target)
    generation: Generation = Field(default_factory=Generation)
    models: list[Model]
    certification: Certification = Field(default_factory=Certification)
    ambience: Ambience = Field(default_factory=Ambience)
    workbench: Workbench = Field(default_factory=Workbench)
    scoring: Scoring = Field(default_factory=Scoring)

    # --- convenience accessors -------------------------------------------
    def model_by_role(self, role: str) -> Model:
        for m in self.models:
            if m.role == role:
                return m
        raise KeyError(f"no model configured for role={role!r}")

    def model_named(self, name: str) -> Model:
        for m in self.models:
            if m.name == name:
                return m
        return self.model_by_role("work")


def _apply_override(raw: dict[str, Any], override: str) -> None:
    if "=" not in override:
        raise ValueError(f"--set must be dotted.key=value, got {override!r}")
    dotted_key, value = override.split("=", 1)
    keys = [part for part in dotted_key.split(".") if part]
    if not keys:
        raise ValueError(f"--set must include a dotted key, got {override!r}")

    cursor: dict[str, Any] = raw
    for key in keys[:-1]:
        existing = cursor.get(key)
        if existing is None:
            existing = {}
            cursor[key] = existing
        if not isinstance(existing, dict):
            raise ValueError(f"--set cannot descend into non-object key {key!r}")
        cursor = existing
    cursor[keys[-1]] = yaml.safe_load(value)


def load_config(path: str | Path, overrides: Sequence[str] | None = None) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    for override in overrides or ():
        _apply_override(raw, override)
    return Config.model_validate(raw)
