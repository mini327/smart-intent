import os
import sqlite3

from smart_intent.application import Application
from smart_intent.config import Config
from smart_intent.errors import DomainError


def test_changed_file_during_read_is_detected(factory, plan, monkeypatch):
    app, path = factory()
    checks = [0]

    def check():
        checks[0] += 1
        if checks[0] == 1:
            with path.open("a", encoding="utf-8") as stream:
                stream.write("\n")

    monkeypatch.setattr(app.records, "deadline", lambda: check)
    result = app.call("query_records", {"plan": plan})
    assert result.error.code == "source_changed"
    assert "records" not in result.data


def test_permission_error_does_not_become_no_records(factory, plan, monkeypatch):
    app, _ = factory()

    def denied(*args, **kwargs):
        raise PermissionError("internal path must not leak")

    monkeypatch.setattr(os, "open", denied)
    result = app.call("query_records", {"plan": plan})
    assert result.error.code == "access_denied"
    assert "internal path" not in result.model_dump_json()


def test_timeout_and_summary_length_are_explicit(factory, plan, monkeypatch):
    app, _ = factory()

    def timeout():
        raise DomainError("query_timeout", "查询超时。", status="failed")

    monkeypatch.setattr(app.records, "deadline", lambda: timeout)
    assert app.call("query_records", {"plan": plan}).error.code == "query_timeout"


def test_small_output_budget_paginates_without_dropping_records(factory, plan):
    app, path = factory(limits={"result_bytes_max": 6000})
    rows = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(r.replace('"test"', '"' + "字" * 500 + '"') for r in rows), encoding="utf-8"
    )
    records = []
    args = {"plan": plan}
    while True:
        result = app.call("query_records", args)
        assert result.status == "succeeded", result
        assert len(result.model_dump_json().encode("utf-8")) <= 6000
        records.extend(r["record_id"] for r in result.data["records"])
        if result.data["complete"]:
            break
        args = {"plan": result.data["normalized_plan"], "cursor": result.data["next_cursor"]}
    assert records == ["e1", "e2", "e3", "e4"]


def test_context_payload_budget_rejects_before_commit(factory, field):
    app, _ = factory(limits={"result_bytes_max": 2048})
    result = app.call("create_task_context", {"fields": {"target": {**field, "value": "x" * 2000}}})
    assert result.status == "rejected"
    assert app.store.db.execute("SELECT count(*) FROM task_contexts").fetchone()[0] == 0


def test_real_database_busy_can_recover_without_version_change(tmp_path, field):
    database = tmp_path / "store.db"
    cfg = Config.model_validate(
        {
            "principal_id": "p",
            "project_id": "x",
            "project_root": str(tmp_path),
            "context": {"mode": "persistent", "database_path": str(database)},
        }
    )
    app = Application(cfg)
    task = app.call("create_task_context", {}).data["task_id"]
    other = sqlite3.connect(database)
    try:
        other.execute("BEGIN IMMEDIATE")
        args = {"task_id": task, "expected_version": 1, "patch": {"target": field}}
        result = app.call("update_task_context", args)
        assert result.error.code == "storage_busy"
        other.rollback()
        assert app.call("update_task_context", args).data["version"] == 2
    finally:
        other.close()
        app.close()
