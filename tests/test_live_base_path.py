"""Prefix-aware playground links (LAN-357).

The portal serves live deployments behind ``/live/{id}/…`` and injects
``LIVE_BASE_PATH=/live/{id}`` into the container. Every internal href/form-action/redirect
(copilot + dossier + the whole /workbench governance surface) must carry that prefix; with
the env unset, rendered output must be byte-identical to today. Routes never move.
"""
import pytest

from synth.live import app as la
from synth.live.paths import base_path, local
from synth.workbench import views as wbviews


def test_base_path_unset_is_empty(monkeypatch):
    monkeypatch.delenv("LIVE_BASE_PATH", raising=False)
    assert base_path() == ""
    assert local("/") == "/"
    assert local("/workbench/runs") == "/workbench/runs"


def test_local_prefixes_every_internal_path(monkeypatch):
    monkeypatch.setenv("LIVE_BASE_PATH", "/live/x")
    assert local("/") == "/live/x/"
    assert local("/ask") == "/live/x/ask"
    assert local("/workbench/runs/abc?err=1") == "/live/x/workbench/runs/abc?err=1"


def test_local_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("LIVE_BASE_PATH", "/live/x/")
    assert local("/workbench") == "/live/x/workbench"


def test_copilot_links_bare_when_unset(monkeypatch):
    monkeypatch.delenv("LIVE_BASE_PATH", raising=False)
    err = la._error_card("oops", ValueError("y"))
    assert 'href="/"' in err and "/live/" not in err


def test_copilot_links_carry_prefix(monkeypatch):
    monkeypatch.setenv("LIVE_BASE_PATH", "/live/x")
    err = la._error_card("oops", ValueError("y"))
    assert 'href="/live/x/"' in err


def test_workbench_nav_bare_when_unset(monkeypatch):
    monkeypatch.delenv("LIVE_BASE_PATH", raising=False)
    nav = wbviews._nav("runs")
    assert "href='/workbench/runs'" in nav
    assert "href='/'" in nav and "href='/dossier'" in nav
    assert "/live/" not in nav


def test_workbench_nav_carries_prefix(monkeypatch):
    monkeypatch.setenv("LIVE_BASE_PATH", "/live/x")
    nav = wbviews._nav("runs")
    assert "href='/live/x/workbench/runs'" in nav
    assert "href='/live/x/'" in nav
    assert "href='/live/x/dossier'" in nav
    assert "href='/workbench/runs'" not in nav


def test_index_page_byte_identical_when_unset(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from synth.config import load_config

    cfg = load_config("config/demo.yaml")

    monkeypatch.delenv("LIVE_BASE_PATH", raising=False)
    bare = TestClient(la.create_app(cfg)).get("/").text
    monkeypatch.setenv("LIVE_BASE_PATH", "")
    empty = TestClient(la.create_app(cfg)).get("/").text
    assert bare == empty
    assert "/live/" not in bare
    assert 'action="/ask"' in bare


def test_index_page_prefixed(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from synth.config import load_config

    cfg = load_config("config/demo.yaml")
    monkeypatch.setenv("LIVE_BASE_PATH", "/live/x")
    text = TestClient(la.create_app(cfg)).get("/").text
    assert 'action="/live/x/ask"' in text
    assert "href='/live/x/dossier'" in text
    assert "href='/live/x/workbench'" in text
