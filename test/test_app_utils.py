from datetime import datetime, timedelta
from pathlib import Path

from src import app_utils


def test_parse_line_ids():
    assert app_utils.parse_line_ids("victoria, central , ,") == ["victoria", "central"]
    assert app_utils.parse_line_ids("") == []


def test_parse_run_time_accepts_empty_and_time_of_day():
    now = datetime.now()
    result_empty = app_utils.parse_run_time("")
    assert isinstance(result_empty, datetime)
    assert abs((result_empty - now).total_seconds()) < 2

    result_time = app_utils.parse_run_time("12:30")
    assert result_time.hour == 12 and result_time.minute == 30


def test_fetch_status_extracts_descriptions(monkeypatch):
    payload = [
        {
            "lineStatuses": [
                {"disruption": {"description": "Issue A"}},
                {"disruption": {"description": "Issue B"}},
            ]
        }
    ]

    class DummyResponse:
        def __init__(self, json_data):
            self._json = json_data

        def raise_for_status(self):
            return None

        def json(self):
            return self._json

    monkeypatch.setattr(app_utils.requests, "get", lambda *args, **kwargs: DummyResponse(payload))
    result = app_utils.fetch_status(["victoria"])
    assert "responses" in result
    assert result["responses"] == ["NO DISRUPTIONS", "Issue A", "Issue B"]


def test_schedule_task_runs_immediately(monkeypatch):
    ran = {}

    def fake_run_task(task_id, line_ids):
        ran["called"] = True
        ran["task_id"] = task_id
        ran["lines"] = line_ids

    monkeypatch.setattr(app_utils, "run_task", fake_run_task)
    run_at = datetime.now() - timedelta(seconds=1)
    created = app_utils.schedule_task("t1", ["victoria"], run_at)

    assert ran["called"] is True
    assert created["task_id"] == "t1"
    assert created["status"] == "scheduled"


def test_update_task_reschedules(monkeypatch):
    # Seed a task
    app_utils.tasks_store["t1"] = {
        "task_id": "t1",
        "lines": ["victoria"],
        "scheduler_time": datetime.now().isoformat(),
        "status": "scheduled",
    }

    removed_jobs = []
    added_jobs = []

    def fake_remove_job(job_id):
        removed_jobs.append(job_id)

    def fake_add_job(*args, **kwargs):
        added_jobs.append((kwargs.get("id"), kwargs.get("run_date"), kwargs.get("args")))

    monkeypatch.setattr(app_utils.scheduler, "remove_job", fake_remove_job)
    monkeypatch.setattr(app_utils.scheduler, "add_job", fake_add_job)

    new_time = datetime.now() + timedelta(hours=1)
    updated = app_utils.update_task("t1", ["central"], new_time)

    assert removed_jobs == ["t1"]
    assert added_jobs and added_jobs[0][0] == "t1"
    assert updated["lines"] == ["central"]
    assert updated["status"] == "scheduled"


def test_delete_task(monkeypatch):
    app_utils.tasks_store["t1"] = {"task_id": "t1"}
    removed_jobs = []
    monkeypatch.setattr(app_utils.scheduler, "remove_job", lambda job_id: removed_jobs.append(job_id))

    assert app_utils.delete_task("t1") is True
    assert "t1" not in app_utils.tasks_store
    assert removed_jobs == ["t1"]
    assert app_utils.delete_task("missing") is False


def test_generate_task_id_unique():
    first = app_utils.generate_task_id()
    app_utils.tasks_store[first] = {"task_id": first}
    second = app_utils.generate_task_id()
    assert first != second
    assert second not in app_utils.tasks_store


def test_load_tasks_from_csv(tmp_path, monkeypatch):
    # Use a dummy CSV and stub schedule_task to avoid scheduler side effects
    monkeypatch.setattr(app_utils.Path, "exists", lambda self: True, raising=False)

    csv_path = tmp_path / "tasks.csv"
    csv_path.write_text('task_id,lines,schedule_time\n1,"victoria","2099-01-01T00:00:00"\n', encoding="utf-8")

    scheduled = []

    def fake_schedule_task(task_id, line_ids, run_at):
        scheduled.append((task_id, line_ids, run_at))
        return {"task_id": task_id, "lines": line_ids, "scheduler_time": run_at.isoformat(), "status": "scheduled"}

    monkeypatch.setattr(app_utils, "schedule_task", fake_schedule_task)

    app_utils.load_tasks_from_csv(csv_path, logger=None)

    assert scheduled, "Expected tasks from CSV to be scheduled"
    assert scheduled[0][0] == "1"
    assert scheduled[0][1] == ["victoria"]
