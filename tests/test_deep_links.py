"""Every Langfuse deep link this kit hands a presenter resolves to a page under v4.

A deep link is a delivery surface: it is clicked in front of a customer, and a 404 there is
not caught by any other gate. v4 reorganised the Langfuse UI, so the routes are pinned here
against :data:`synth.workbench.links.ROUTES` — the set checked against the v4 app's own
routing — and every producer of a project-scoped URL in the kit is held to it (portal #212).
"""
from __future__ import annotations

import re

import pytest

from synth.workbench.links import ROUTES, Links

BASE, PID = "https://demo.langfuse.example", "clproj0000demo0001"


@pytest.fixture()
def lf():
    return Links(BASE, PID)


def _suffix(url: str) -> str:
    assert url.startswith(f"{BASE}/project/{PID}/"), url
    return url[len(f"{BASE}/project/{PID}/"):]


def _template(suffix: str) -> str:
    """The suffix with its id segments replaced by ``{}`` — 'datasets/ds-1/items' becomes
    'datasets/{}/items'. Known section names stay literal; anything else is an id."""
    words = {"traces", "sessions", "scores", "datasets", "items", "experiments", "prompts",
             "annotation-queues", "evals", "dashboards"}
    return "/".join(p if p in words else "{}" for p in suffix.split("/") if p != "") \
        if suffix else ""


def test_every_link_helper_builds_a_route_that_exists(lf):
    built = {
        "project": lf.project(),
        "datasets": lf.datasets(),
        "dataset": lf.dataset("ds-1"),
        "dataset_runs": lf.dataset_runs("ds-1"),
        "dataset_item": lf.dataset_item("ds-1", "item0001"),
        "trace": lf.trace("t-1"),
        "prompt": lf.prompt("analyst-copilot"),
        "prompts": lf.prompt(""),
        "queues": lf.queues(),
        "queue": lf.queue("q-1"),
        "evals": lf.evals(),
    }
    for name, url in built.items():
        assert _template(_suffix(url)) in ROUTES, f"{name} -> {url}"


def test_dataset_runs_points_at_the_experiments_tab(lf):
    """v4 renamed a dataset run to an experiment and moved the list. ``datasets/{id}/runs``
    has no index page at all — only ``runs/{runId}`` — so the pre-v4 link 404'd."""
    assert lf.dataset_runs("ds-1") == f"{BASE}/project/{PID}/datasets/ds-1/experiments"
    assert "/runs" not in lf.dataset_runs("ds-1")


def test_dataset_link_names_the_tab_it_lands_on(lf):
    """The bare ``datasets/{id}`` is an alias that redirects to Items."""
    assert lf.dataset("ds-1") == f"{BASE}/project/{PID}/datasets/ds-1/items"


def test_links_stay_empty_without_a_project_id():
    """Never a broken link: an unresolved project renders no anchor at all."""
    blank = Links(BASE, "")
    assert blank.trace("t-1") == "" and blank.dataset_runs("ds-1") == ""


def test_demo_script_links_use_known_routes():
    """The presenter-facing artefacts build their own URLs from ``script.py``'s ``ui()``
    rather than through ``Links``; the suffixes they pass are held to the same route set."""
    from synth import script

    src = (script.build_context.__doc__ or "") + open(script.__file__).read()
    suffixes = set(re.findall(r"_deep_link\(state, [\"']([a-z-]+)[\"']", src))
    suffixes |= set(re.findall(r"_deep_link\(state, f[\"']([a-z-]+)/", src))
    assert suffixes, "no deep links found — did the helper get renamed?"
    for s in suffixes:
        assert _template(s) in ROUTES or _template(f"{s}/x") in ROUTES, s


def test_templates_named_in_the_runbook_use_known_routes():
    """``ui('…')`` calls inside the rendered runbook / walkthrough templates."""
    import pathlib

    used = set()
    for tpl in pathlib.Path("templates").glob("*.j2"):
        text = tpl.read_text()
        used |= set(re.findall(r"(?:ui|htmllink)\(\s*'([a-z-]+)'", text))
        used |= set(re.findall(r"(?:ui|htmllink)\(\s*'([a-z-]+)/'\s*\+", text))
    assert used
    for s in used:
        assert _template(s) in ROUTES or _template(f"{s}/x") in ROUTES, s


def test_a_stored_pre_v4_link_is_repaired_before_it_is_rendered():
    """A workbench run record stores the runs URL it was given at run time, and the
    workbench renders the stored value rather than rebuilding it. A record written before
    the cutover carries the retired ``/runs`` route, so it is repaired on the way out."""
    stale = f"{BASE}/project/{PID}/datasets/ds-golden/runs"
    assert Links.upgrade(stale) == f"{BASE}/project/{PID}/datasets/ds-golden/experiments"
    assert _template(_suffix(Links.upgrade(stale))) in ROUTES
    # Anything already on a v4 route, or not a dataset link at all, is left alone.
    for url in (f"{BASE}/project/{PID}/datasets/ds-golden/experiments",
                f"{BASE}/project/{PID}/traces/t-1", ""):
        assert Links.upgrade(url) == url

