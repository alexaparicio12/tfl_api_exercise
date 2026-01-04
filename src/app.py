from pathlib import Path

from flask import Flask, jsonify, request
import requests

from src.app_utils import (
    delete_task,
    fetch_disruptions,
    fetch_status,
    generate_task_id,
    load_tasks_from_csv,
    parse_line_ids,
    parse_run_time,
    schedule_task,
    scheduler,
    tasks_store,
    update_task,
)


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
            "task_id": "morning-run",  # optional; auto-generated if missing
            "lines": "victoria,central",
            "scheduler_time": "2024-05-01T08:30:00"
        }
        """
        payload = request.get_json(silent=True) or {}
        task_id = payload.get("task_id")
        line_ids_raw = payload.get("lines", "")
        time_str = payload.get("scheduler_time", "")

        provided_id = bool(task_id)
        if not provided_id:
            task_id = generate_task_id()

        line_ids = parse_line_ids(line_ids_raw if isinstance(line_ids_raw, str) else "")
        if not line_ids:
            return jsonify(error="lines must be a comma-separated string"), 400
        try:
            run_at = parse_run_time(time_str)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

        try:
            task = schedule_task(task_id, line_ids, run_at)
        except ValueError:
            if provided_id:
                return jsonify(error="task_id already exists"), 409
            # regenerate if collision on auto id
            task_id = generate_task_id()
            task = schedule_task(task_id, line_ids, run_at)

        return jsonify(task), 201

    @app.route("/tasks", methods=["GET"])
    def list_tasks():
        return jsonify(list(tasks_store.values()))

    @app.route("/tasks/<task_id>", methods=["GET"])
    def get_task(task_id: str):
        task = tasks_store.get(task_id)
        if not task:
            return jsonify(error="task not found"), 404
        return jsonify(task)

    @app.route("/tasks/<task_id>", methods=["PATCH"])
    def patch_task(task_id: str):
        payload = request.get_json(silent=True) or {}
        lines_raw_provided = "lines" in payload
        time_provided = "scheduler_time" in payload

        if not lines_raw_provided and not time_provided:
            return jsonify(error="Provide 'lines' and/or 'scheduler_time' to update"), 400

        new_lines = None
        if lines_raw_provided:
            lines_raw = payload.get("lines", "")
            new_lines = parse_line_ids(lines_raw if isinstance(lines_raw, str) else "")
            if not new_lines:
                return jsonify(error="lines must be a comma-separated string"), 400

        run_at = None
        if time_provided:
            time_str = payload.get("scheduler_time", "")
            try:
                run_at = parse_run_time(time_str)
            except ValueError as exc:
                return jsonify(error=str(exc)), 400

        try:
            updated = update_task(task_id, new_lines, run_at)
        except KeyError:
            return jsonify(error="task not found"), 404

        return jsonify(updated)

    @app.route("/tasks/<task_id>", methods=["DELETE"])
    def remove_task(task_id: str):
        removed = delete_task(task_id)
        if not removed:
            return jsonify(error="task not found"), 404
        return jsonify(message="task deleted")

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5555)
