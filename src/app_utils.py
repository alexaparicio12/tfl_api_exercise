import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote
import uuid

import requests
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError

# Shared in-memory store and scheduler
tasks_store: Dict[str, Dict[str, Any]] = {}
scheduler = BackgroundScheduler(executors={"default": ThreadPoolExecutor(4)})


def parse_line_ids(raw_lines: str) -> List[str]:
    return [part.strip() for part in raw_lines.split(",") if part.strip()]


def fetch_disruptions(line_ids: List[str]) -> Any:
    encoded_lines = quote(",".join(line_ids), safe="")
    url = f"https://api.tfl.gov.uk/Line/{encoded_lines}/Disruption"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def fetch_status(line_ids: List[str]) -> Any:
    encoded_lines = quote(",".join(line_ids), safe="")
    url = f"https://api.tfl.gov.uk/Line/{encoded_lines}/Status"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    payload = response.json()

    responses: List[str] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            line_statuses = item.get("lineStatuses", [])
            if isinstance(line_statuses, list):
                for status in line_statuses:
                    if not isinstance(status, dict):
                        continue
                    disruption = status.get("disruption")
                    if isinstance(disruption, dict):
                        desc = disruption.get("description")
                        if desc:
                            responses.append(desc)
    return {"responses": ["NO DISRUPTIONS"] + responses}


def run_task(task_id: str, line_ids: List[str]) -> None:
    """Job executed by the scheduler."""
    #@todo: need to handle potential failures of these fetch commands
    try:
        result = fetch_disruptions(line_ids)
        if result == []:
            result = fetch_status(line_ids)
        outcome = {"status": "completed", "result": result, "ran_at": datetime.now().isoformat()}
    except Exception as exc:  # noqa: BLE001 broad to capture network failures
        outcome = {"status": "failed", "error": str(exc), "ran_at": datetime.now().isoformat()}

    if task_id in tasks_store:
        tasks_store[task_id].update(outcome)


def parse_run_time(time_str: str) -> datetime:
    """
    Accepts either ISO datetime (YYYY-MM-DDTHH:MM:SS) or time-of-day (HH:MM).
    Falls back to "now" if empty string is provided.
    """
    if not time_str:
        return datetime.now()
    try:
        return datetime.fromisoformat(time_str)
    except ValueError:
        pass
    try:
        today_prefix = datetime.now().strftime("%Y-%m-%dT")
        return datetime.fromisoformat(f"{today_prefix}{time_str}")
    except ValueError as exc:
        raise ValueError("time must be ISO datetime or HH:MM") from exc


def schedule_task(task_id: str, line_ids: List[str], run_at: datetime) -> Dict[str, Any]:
    if task_id in tasks_store:
        raise ValueError("task_id already exists")
    tasks_store[task_id] = {
        "task_id": task_id,
        "lines": line_ids,
        "scheduler_time": run_at.isoformat(),
        "status": "scheduled",
    }

    if run_at <= datetime.now():
        run_task(task_id, line_ids)
    else:
        scheduler.add_job(
            run_task,
            "date",
            run_date=run_at,
            args=[task_id, line_ids],
            id=task_id,
            replace_existing=True,
        )

    return tasks_store[task_id]


def update_task(task_id: str, line_ids: Optional[List[str]], run_at: Optional[datetime]) -> Dict[str, Any]:
    """Update lines/time for an existing task and reschedule it."""
    task = tasks_store.get(task_id)
    if not task:
        raise KeyError("task not found")

    # Use existing lines/time when not provided
    effective_lines = line_ids if line_ids is not None else task["lines"]
    effective_time = run_at or datetime.fromisoformat(task["scheduler_time"])

    # Cancel existing scheduled job if any
    try:
        scheduler.remove_job(task_id)
    except JobLookupError:
        pass

    task["lines"] = effective_lines
    task["scheduler_time"] = effective_time.isoformat()
    task["status"] = "scheduled"
    task.pop("result", None)
    task.pop("error", None)
    task.pop("ran_at", None)

    if effective_time <= datetime.now():
        run_task(task_id, effective_lines)
    else:
        scheduler.add_job(
            run_task,
            "date",
            run_date=effective_time,
            args=[task_id, effective_lines],
            id=task_id,
            replace_existing=True,
        )

    return dict(tasks_store[task_id])


def delete_task(task_id: str) -> bool:
    """Remove task from store and cancel scheduled job if present."""
    try:
        scheduler.remove_job(task_id)
    except JobLookupError:
        pass

    existed = task_id in tasks_store
    if existed:
        tasks_store.pop(task_id, None)
    return existed


def load_tasks_from_csv(csv_path: Path, logger) -> None:
    if not csv_path.exists():
        return

    with csv_path.open(newline="", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            task_id = (row.get("task_id") or "").strip()
            line_ids_raw = (row.get("lines") or "").strip()
            time_str = (row.get("schedule_time") or "").strip()
            if not task_id or not line_ids_raw:
                continue
            line_ids = parse_line_ids(line_ids_raw)
            if not line_ids:
                continue
            try:
                run_at = parse_run_time(time_str)
                schedule_task(task_id, line_ids, run_at)
            except Exception as exc:  # noqa: BLE001 catch parse errors
                if logger:
                    logger.warning("Skipping task %s from CSV: %s", task_id, exc)


def generate_task_id() -> str:
    """Generate a unique task_id not present in tasks_store."""
    while True:
        candidate = uuid.uuid4().hex
        if candidate not in tasks_store:
            return candidate
