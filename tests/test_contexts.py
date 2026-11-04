from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from smart_intent.application import Application
from smart_intent.config import Config


def test_update_conflict_clear_and_no_resurrection(factory, field):
    app, _ = factory()
    created = app.call("create_task_context", {"fields": {"target": field}})
    assert created.status == "created"
    task = created.data["task_id"]
    assert created.data["version"] == 1
    update = {"task_id": task, "expected_version": 1, "patch": {"action": field}}
    assert app.call("update_task_context", update).data["version"] == 2
    assert app.call("update_task_context", update).status == "conflict"
    assert (
        app.call("clear_task_context", {"task_id": task, "expected_version": 1}).status
        == "conflict"
    )
    assert (
        app.call("clear_task_context", {"task_id": task, "expected_version": 2}).status == "cleared"
    )
    assert (
        app.call("clear_task_context", {"task_id": task, "expected_version": 2}).status == "cleared"
    )
    assert app.call("update_task_context", update).status == "not_found"


def test_scope_isolation_and_no_model_identity_override(factory):
    app, _ = factory()
    task = app.call("create_task_context", {}).data["task_id"]
    assert (
        app.call("get_task_context", {"task_id": task, "principal_id": "other"}).status
        == "rejected"
    )
    other, _ = factory(principal_id="other")
    assert other.call("get_task_context", {"task_id": task}).status == "not_found"


def test_persistent_scope_survives_restart_and_isolated(tmp_path, field):
    cfg = Config.model_validate(
        {
            "principal_id": "p",
            "project_id": "one",
            "project_root": str(tmp_path),
            "context": {"mode": "persistent", "database_path": str(tmp_path / "state.db")},
        }
    )
    app = Application(cfg)
    task = app.call("create_task_context", {"fields": {"target": field}}).data["task_id"]
    app.close()
    app = Application(cfg)
    assert app.call("get_task_context", {"task_id": task}).data["fields"]["target"]["value"] == "a"
    app.close()
    for key in ("principal_id", "project_id"):
        other = Application(cfg.model_copy(update={key: "other"}))
        assert other.call("get_task_context", {"task_id": task}).status == "not_found"
        other.close()


def test_expiry_and_ephemeral_restart(factory, field):
    app, _ = factory(context={"ttl_seconds": 10})
    task = app.call("create_task_context", {"fields": {"target": field}}).data["task_id"]
    factory.clock[0] += timedelta(seconds=10)
    assert app.call("get_task_context", {"task_id": task}).status == "expired"
    other, _ = factory()
    assert other.call("get_task_context", {"task_id": task}).status == "not_found"


def test_field_expiry_and_invalidate_previous_result(factory, field):
    app, _ = factory()
    short = {**field, "expires_at": (factory.clock[0] + timedelta(seconds=1)).isoformat()}
    task = app.call(
        "create_task_context", {"fields": {"target": field, "last_result": short}}
    ).data["task_id"]
    factory.clock[0] += timedelta(seconds=1)
    assert "last_result" not in app.call("get_task_context", {"task_id": task}).data["fields"]
    app.call(
        "update_task_context",
        {"task_id": task, "expected_version": 1, "patch": {"last_result": field}},
    )
    result = app.call(
        "update_task_context",
        {"task_id": task, "expected_version": 2, "patch": {"time_range": field}},
    )
    assert "last_result" not in result.data["fields"]
    assert "target" in result.data["fields"]


def test_bad_fields_rejected_before_mutation(factory, field):
    app, _ = factory()
    assert app.call("create_task_context", {"fields": {"credentials": field}}).status == "rejected"
    assert (
        app.call(
            "create_task_context",
            {"fields": {"target": {**field, "expires_at": "2026-11-01T00:00:00Z"}}},
        ).status
        == "rejected"
    )
    assert (
        app.call(
            "create_task_context", {"fields": {"target": {**field, "value": "x" * 40000}}}
        ).status
        == "rejected"
    )


def test_concurrent_same_version_only_one_wins(factory, field):
    app, _ = factory()
    task = app.call("create_task_context", {}).data["task_id"]
    args = {"task_id": task, "expected_version": 1, "patch": {"target": field}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: app.call("update_task_context", args).status, range(2)))
    assert sorted(results) == ["conflict", "updated"]
