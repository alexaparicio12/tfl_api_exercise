import types
import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path for `src` imports
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import app_utils


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    # Fresh store and stub scheduler per test session
    fresh_store = {}
    stub_scheduler = types.SimpleNamespace(
        running=False,
        add_job=lambda *args, **kwargs: None,
        remove_job=lambda *args, **kwargs: None,
        start=lambda: None,
    )

    # Patch app_utils
    monkeypatch.setattr(app_utils, "tasks_store", fresh_store)
    monkeypatch.setattr(app_utils, "scheduler", stub_scheduler)
    # Avoid loading real CSV
    monkeypatch.setattr(app_utils.Path, "exists", lambda self: False)

    # Keep app module in sync with patched objects when imported
    import src.app as app_module

    monkeypatch.setattr(app_module, "tasks_store", fresh_store)
    monkeypatch.setattr(app_module, "scheduler", stub_scheduler)

    yield
    fresh_store.clear()
