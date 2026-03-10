import asyncio
import builtins
import os
import sys
import importlib

# Ensure repo root is on sys.path so `app` package can be imported
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
import types


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP Error {self.status_code}")


class DummyAsyncClient:
    def __init__(self, responses):
        # responses is an iterator or list of responses to return on successive .get calls
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, auth=None):
        if not self._responses:
            return DummyResponse(200, {})
        return self._responses.pop(0)


def test_fetch_items_success(monkeypatch):
    payload = [
        {"lab": "lab-01", "task": None, "title": "Lab 1", "type": "lab"},
        {"lab": "lab-01", "task": "task-1", "title": "Task 1", "type": "task"},
    ]

    dummy = DummyResponse(200, payload)

    # Inject fake settings module before importing etl to avoid requiring pydantic
    fake_settings_mod = types.ModuleType("app.settings")
    fake_settings_mod.settings = types.SimpleNamespace(
        autochecker_api_url="https://fake-api.local",
        autochecker_email="test@example.com",
        autochecker_password="secret",
    )
    monkeypatch.setitem(sys.modules, "app.settings", fake_settings_mod)

    etl = importlib.import_module("app.etl")
    monkeypatch.setattr(etl.httpx, "AsyncClient", lambda: DummyAsyncClient([dummy]))

    result = asyncio.run(etl.fetch_items())
    assert isinstance(result, list)
    assert result == payload


def test_fetch_logs_pagination(monkeypatch):
    # First page: two logs, has_more True
    first_payload = {
        "logs": [{"id": 1, "submitted_at": "2025-01-01T00:00:00Z"}],
        "count": 1,
        "has_more": True,
    }
    # Second page: final logs, has_more False
    second_payload = {
        "logs": [{"id": 2, "submitted_at": "2025-01-01T00:01:00Z"}],
        "count": 1,
        "has_more": False,
    }

    r1 = DummyResponse(200, first_payload)
    r2 = DummyResponse(200, second_payload)

    # Inject fake settings and import etl
    fake_settings_mod = types.ModuleType("app.settings")
    fake_settings_mod.settings = types.SimpleNamespace(
        autochecker_api_url="https://fake-api.local",
        autochecker_email="test@example.com",
        autochecker_password="secret",
    )
    monkeypatch.setitem(sys.modules, "app.settings", fake_settings_mod)
    etl = importlib.import_module("app.etl")
    monkeypatch.setattr(etl.httpx, "AsyncClient", lambda: DummyAsyncClient([r1, r2]))

    result = asyncio.run(etl.fetch_logs())
    assert isinstance(result, list)
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert ids == {1, 2}
