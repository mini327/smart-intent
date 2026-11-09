"""Regressions for the scoped configuration, cursor and locking review."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from smart_intent.application import Application
from smart_intent.config import load_config
from smart_intent.records import RecordSource


def test_config_file_changes_only_affect_new_instances(factory, plan, tmp_path):
    original, source = factory()
    config_path = tmp_path / "config.toml"

    def write(source_id):
        quote = lambda value: json.dumps(str(value).replace("\\", "/"))  # noqa: E731
        config_path.write_text(
            'principal_id="local"\nproject_id="project"\n'
            f'project_root={quote(source.parent)}\n[[sources]]\nid="{source_id}"\n'
            f"path={quote(source)}\nallowed_root={quote(source.parent)}\n"
            'classification="test"\n',
            encoding="utf-8",
        )

    write("events")
    old = Application(load_config(config_path))
    new = None
    try:
        assert old.call("query_records", {"plan": plan}).status == "succeeded"
        write("replacement")
        replacement = {**plan, "source_id": "replacement"}
        assert old.call("query_records", {"plan": plan}).status == "succeeded"
        assert old.call("query_records", {"plan": replacement}).error.code == "access_denied"
        new = Application(load_config(config_path))
        assert new.call("query_records", {"plan": replacement}).status == "succeeded"
        assert new.call("query_records", {"plan": plan}).error.code == "access_denied"
        assert source.exists()
    finally:
        old.close()
        if new:
            new.close()
    assert original.records.cache == {}


def test_deadline_is_created_after_lock_acquisition(factory, plan, monkeypatch):
    app, _ = factory()
    original_lock = app.lock
    attempted = threading.Event()
    acquired = threading.Event()
    called = threading.Event()
    calls = []
    original_deadline = app.records.deadline

    class ObservedLock:
        def __enter__(self):
            attempted.set()
            original_lock.acquire()
            acquired.set()
            return self

        def __exit__(self, *args):
            original_lock.release()

    def deadline():
        assert acquired.is_set()
        calls.append(time.monotonic())
        called.set()
        return original_deadline()

    monkeypatch.setattr(app, "lock", ObservedLock())
    monkeypatch.setattr(app.records, "deadline", deadline)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with original_lock:
            future = pool.submit(app.call, "query_records", {"plan": plan})
            assert attempted.wait(5), "worker never attempted the application lock"
            assert not acquired.is_set()
            assert not called.is_set()
            released_at = time.monotonic()
        result = future.result(timeout=5)
    assert result.status == "succeeded"
    assert len(calls) == 1 and calls[0] >= released_at


def test_cache_eviction_uses_access_order_and_weight(factory, plan):
    app, _ = factory()
    args = {"plan": plan, "page_size": 1}
    a = app.call("query_records", args)
    b = app.call("query_records", args)
    a_id, b_id = a.data["snapshot_id"], b.data["snapshot_id"]
    cache = app.records.cache
    # Isolate eviction from configuration loading: set the in-memory test budget
    # from measured weights, without relying on CPython-specific object sizes.
    budget = cache[a_id].weight + cache[b_id].weight + 1
    app.config.limits.snapshot_cache_bytes_max = budget
    a_args = {**args, "plan": a.data["normalized_plan"], "cursor": a.data["next_cursor"]}
    assert app.call("query_records", a_args).status == "succeeded"
    assert list(cache) == [b_id, a_id]
    c = app.call("query_records", args)
    assert c.status == "succeeded"
    assert list(cache) == [a_id, c.data["snapshot_id"]]
    assert sum(snapshot.weight for snapshot in cache.values()) <= budget
    b_args = {**args, "plan": b.data["normalized_plan"], "cursor": b.data["next_cursor"]}
    assert app.call("query_records", b_args).error.code == "cursor_expired"
    assert app.call("query_records", a_args).status == "succeeded"


def test_snapshot_ttl_is_fixed_and_uses_monotonic_time(factory, plan, monkeypatch):
    import smart_intent.records as records

    now = [100.0]
    # Replace this module's time binding, not the interpreter's shared clock.
    monkeypatch.setattr(records, "time", SimpleNamespace(monotonic=lambda: now[0]))
    app, _ = factory(limits={"snapshot_ttl_seconds": 2})
    first = app.call("query_records", {"plan": plan, "page_size": 1})
    args = {"plan": first.data["normalized_plan"], "cursor": first.data["next_cursor"]}
    now[0] = 101.0
    assert app.call("query_records", args).status == "succeeded"
    now[0] = 102.0
    assert app.call("query_records", args).error.code == "cursor_expired"


def test_cursor_replay_and_instance_replacement(factory, plan):
    app, _ = factory()
    first = app.call("query_records", {"plan": plan, "page_size": 1})
    args = {
        "plan": first.data["normalized_plan"],
        "cursor": first.data["next_cursor"],
        "page_size": 1,
    }
    second = app.call("query_records", args)
    replay = app.call("query_records", args)
    assert replay.data["snapshot_id"] == second.data["snapshot_id"]
    assert replay.data["records"] == second.data["records"]
    rebuilt = Application(app.config)
    try:
        assert rebuilt.call("query_records", args).error.code == "cursor_invalid"
    finally:
        rebuilt.close()
    app.records = RecordSource(app.config, app.clock)
    assert app.call("query_records", args).error.code == "cursor_expired"
