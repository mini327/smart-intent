import json
from datetime import UTC, datetime

import pytest

from smart_intent.application import Application
from smart_intent.config import Config


@pytest.fixture
def factory(tmp_path):
    instances = []
    clock = [datetime(2026, 11, 4, 1, tzinfo=UTC)]

    def make(rows=None, **overrides):
        root = tmp_path / str(len(instances))
        root.mkdir()
        path = root / "events.jsonl"
        if rows is None:
            rows = [
                {
                    "id": "e1",
                    "target_id": "a",
                    "target_name": "同名",
                    "occurred_at": "2026-11-03T00:00:00+08:00",
                    "status": "failed",
                    "category": "delivery",
                    "summary": "test",
                },
                {
                    "id": "e2",
                    "target_id": "b",
                    "target_name": "同名",
                    "occurred_at": "2026-11-03T09:00:00+08:00",
                    "status": "failed",
                    "category": "scheduler",
                    "summary": "test",
                },
                {
                    "id": "e3",
                    "target_id": "a",
                    "target_name": "旧名",
                    "occurred_at": "2026-11-04T00:00:00+08:00",
                    "status": "succeeded",
                    "category": "delivery",
                    "summary": "test",
                },
                {
                    "id": "e4",
                    "target_id": "a",
                    "target_name": "同名",
                    "occurred_at": "2026-12-01T00:00:00+08:00",
                    "status": "failed",
                    "category": "delivery",
                    "summary": "test",
                },
            ]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        cfg = {
            "principal_id": "local",
            "project_id": "project",
            "project_root": str(root),
            "sources": [
                {
                    "id": "events",
                    "path": str(path),
                    "allowed_root": str(root),
                    "classification": "test",
                }
            ],
        }
        cfg.update(overrides)
        app = Application(Config.model_validate(cfg), clock=lambda: clock[0])
        instances.append(app)
        return app, path

    make.clock = clock
    yield make
    for app in instances:
        app.close()


@pytest.fixture
def plan():
    return {"source_id": "events", "action": "query", "target": {"mode": "all"}}


@pytest.fixture
def field():
    return {
        "value": "a",
        "confirmation": "user_explicit",
        "origin": {"kind": "user_message", "ref": "message-1"},
    }
