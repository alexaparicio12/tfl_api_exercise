import json

import pytest

from src.app import create_app
from src import app_utils


@pytest.fixture
def client():
    app = create_app()
    app.config.update({"TESTING": True})
    return app.test_client()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_create_and_get_task(client, monkeypatch):
    # Stub disruption/status fetch to avoid network
    monkeypatch.setattr(app_utils, "fetch_disruptions", lambda lines: {"ok": lines})
    monkeypatch.setattr(app_utils, "fetch_status", lambda lines: {"responses": ["none"]})
    import src.app as app_module

    monkeypatch.setattr(app_module, "fetch_disruptions", app_utils.fetch_disruptions)
    monkeypatch.setattr(app_module, "fetch_status", app_utils.fetch_status)

    payload = {"lines": "victoria,central", "scheduler_time": ""}
    resp = client.post("/tasks", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 201
    created = resp.get_json()
    task_id = created["task_id"]

    # Fetch it back
    resp_get = client.get(f"/tasks/{task_id}")
    assert resp_get.status_code == 200
    fetched = resp_get.get_json()
    assert fetched["task_id"] == task_id
    assert fetched["lines"] == ["victoria", "central"]


def test_delete_task(client, monkeypatch):
    monkeypatch.setattr(app_utils, "fetch_disruptions", lambda lines: {"ok": lines})
    import src.app as app_module

    monkeypatch.setattr(app_module, "fetch_disruptions", app_utils.fetch_disruptions)
    payload = {"lines": "victoria", "scheduler_time": ""}
    resp = client.post("/tasks", data=json.dumps(payload), content_type="application/json")
    task_id = resp.get_json()["task_id"]

    resp_del = client.delete(f"/tasks/{task_id}")
    assert resp_del.status_code == 200
    assert resp_del.get_json()["message"] == "task deleted"

    resp_get = client.get(f"/tasks/{task_id}")
    assert resp_get.status_code == 404


def test_patch_task(client, monkeypatch):
    monkeypatch.setattr(app_utils, "fetch_disruptions", lambda lines: {"ok": lines})
    import src.app as app_module

    monkeypatch.setattr(app_module, "fetch_disruptions", app_utils.fetch_disruptions)
    payload = {"lines": "victoria", "scheduler_time": ""}
    resp = client.post("/tasks", data=json.dumps(payload), content_type="application/json")
    task_id = resp.get_json()["task_id"]

    patch_payload = {"lines": "bakerloo", "scheduler_time": "2099-01-01T00:00:00"}
    resp_patch = client.patch(
        f"/tasks/{task_id}", data=json.dumps(patch_payload), content_type="application/json"
    )
    assert resp_patch.status_code == 200
    patched = resp_patch.get_json()
    assert patched["lines"] == ["bakerloo"]
