import csv
from datetime import datetime
import threading
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from flask import Flask, jsonify, request
import requests
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler


tasks_store: Dict[str, Dict[str, Any]] = {}
store_lock = threading.Lock()
scheduler = BackgroundScheduler(executors={"default": ThreadPoolExecutor(4)})


def parse_line_ids(raw_lines: str) -> List[str]:
    return [part.strip() for part in raw_lines.split(",") if part.strip()]


def fetch_disruptions(line_ids: List[str]) -> Any:
    encoded_lines = quote(",".join(line_ids), safe="")
    url = f"https://api.tfl.gov.uk/Line/{encoded_lines}/Disruption"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def run_task(task_id: str, line_ids: List[str]) -> None:
    """Job executed by the scheduler."""
    try:
        result = fetch_disruptions(line_ids)
        outcome = {"status": "completed", "result": result, "ran_at": datetime.now().isoformat()}
    except Exception as exc:  # noqa: BLE001 broad to capture network failures
        outcome = {"status": "failed", "error": str(exc), "ran_at": datetime.now().isoformat()}

    with store_lock:
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
    with store_lock:
        if task_id in tasks_store:
            raise ValueError("task_id already exists")
        tasks_store[task_id] = {
            "task_id": task_id,
            "line_ids": line_ids,
            "time_to_run": run_at.isoformat(),
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

    with store_lock:
        return tasks_store[task_id]


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


def create_app() -> Flask:
    app = Flask(__name__)

    if not scheduler.running:
        scheduler.start()

    load_tasks_from_csv(Path("data/tasks.csv"), app.logger)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(status="ok")

    @app.route("/hello", methods=["GET"])
    def hello():
        return jsonify(message="hello world")

    @app.route("/disruptions", methods=["GET"])
    def disruptions():
        """
        Proxy to the TFL Line Disruption API.
        Call with:
          - ?lines=victoria
          - ?lines=victoria,central
        """
        lines = request.args.get("lines", "")
        line_ids = parse_line_ids(lines)
        if not line_ids:
            return jsonify(error="Provide lines via ?lines=a or ?lines=a,b"), 400

        try:
            result = fetch_disruptions(line_ids)
        except requests.RequestException as exc:
            return jsonify(error="failed to reach TFL API", detail=str(exc)), 502

        return jsonify(result)

    @app.route("/tasks", methods=["POST"])
    def create_task():
        """
        Schedule a disruption fetch at a given time.
        Body:
        {
            "task_id": "morning-run",
            "line_ids": "victoria,central",
            "time_to_run": "2024-05-01T08:30:00"
        }
        """
        payload = request.get_json(silent=True) or {}
        task_id = payload.get("task_id")
        line_ids_raw = payload.get("line_ids", "")
        time_str = payload.get("time_to_run", "")

        if not task_id:
            return jsonify(error="task_id is required"), 400

        line_ids = parse_line_ids(line_ids_raw if isinstance(line_ids_raw, str) else "")
        if not line_ids:
            return jsonify(error="line_ids must be a comma-separated string"), 400
        try:
            run_at = parse_run_time(time_str)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

        try:
            task = schedule_task(task_id, line_ids, run_at)
        except ValueError:
            return jsonify(error="task_id already exists"), 409

        return jsonify(task), 201

    @app.route("/tasks", methods=["GET"])
    def list_tasks():
        with store_lock:
            return jsonify(list(tasks_store.values()))

    @app.route("/tasks/<task_id>", methods=["GET"])
    def get_task(task_id: str):
        with store_lock:
            task = tasks_store.get(task_id)
        if not task:
            return jsonify(error="task not found"), 404
        return jsonify(task)

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5555)
