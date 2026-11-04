import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime

from .config import Config
from .errors import DomainError
from .models import ContextField
from .time_ranges import aware

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_contexts (
 principal_id TEXT NOT NULL, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version >= 1), payload_json TEXT NOT NULL,
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(principal_id, project_id, task_id)
);
CREATE INDEX IF NOT EXISTS task_contexts_expiry ON task_contexts(expires_at);
"""


class ContextStore:
    def __init__(self, config: Config, clock):
        self.config, self.clock = config, clock
        self.lock = threading.RLock()
        path = config.context.database_path
        if path:
            if str(path).startswith("\\\\"):
                raise ValueError("Network databases are unsupported")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise ValueError("Database symlinks are unsupported")
        self.db = sqlite3.connect(
            str(path) if path else ":memory:",
            timeout=0.2,
            check_same_thread=False,
            isolation_level=None,
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=200")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("Unsupported database version; migration required")
        self.db.executescript(SCHEMA + "PRAGMA user_version=1;")
        self.db.execute("DELETE FROM task_contexts WHERE expires_at <= ?", (self.now(),))

    def now(self):
        return int(aware(self.clock()).timestamp())

    @property
    def scope(self):
        return (self.config.principal_id, self.config.project_id)

    def _row(self, task_id):
        return self.db.execute(
            "SELECT * FROM task_contexts WHERE principal_id=? AND project_id=? AND task_id=?",
            (*self.scope, task_id),
        ).fetchone()

    def _active(self, task_id):
        row = self._row(task_id)
        if row is None:
            raise DomainError("task_not_found", "当前范围内没有该任务。", status="not_found")
        if row["expires_at"] <= self.now():
            raise DomainError("task_expired", "任务已过期，请创建新任务。", status="expired")
        return row

    def _fields(self, fields: dict[str, ContextField], expires):
        now = self.now()
        result = {}
        for key, field in fields.items():
            expiry = int(aware(field.expires_at).timestamp()) if field.expires_at else expires
            if expiry > expires or expiry <= now:
                raise DomainError(
                    "field_expiry_invalid", "字段有效期必须在任务有效期内。", status="rejected"
                )
            result[key] = {
                **field.model_dump(mode="json"),
                "observed_at": datetime.fromtimestamp(now, UTC).isoformat(),
                "expires_at": datetime.fromtimestamp(expiry, UTC).isoformat(),
            }
        return result

    def _encode(self, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > min(32768, self.config.limits.result_bytes_max - 1024):
            raise DomainError("context_too_large", "任务上下文不能超过32 KiB。", status="rejected")
        return encoded

    def _view(self, row):
        payload = json.loads(row["payload_json"])
        payload["fields"] = {
            key: value
            for key, value in payload["fields"].items()
            if datetime.fromisoformat(value["expires_at"]).timestamp() > self.now()
        }
        return {
            "task_id": row["task_id"],
            "version": row["version"],
            **payload,
            "project_id": row["project_id"],
            "expires_at": datetime.fromtimestamp(row["expires_at"], UTC).isoformat(),
            "storage_mode": self.config.context.mode,
        }

    def create(self, args):
        with self.lock:
            now = self.now()
            expires = now + self.config.context.ttl_seconds
            payload = self._encode(
                {"label": args.label, "fields": self._fields(args.fields, expires)}
            )
            task_id = uuid.uuid4().hex
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "INSERT INTO task_contexts VALUES (?,?,?,?,?,?,?,?)",
                    (*self.scope, task_id, 1, payload, expires, now, now),
                )
                # Cleanup and insert must commit together; a cleanup failure must not leave
                # a created task whose handle was never returned to the caller.
                self.db.execute("DELETE FROM task_contexts WHERE expires_at < ?", (now - 86400,))
                result = self._view(self._active(task_id))
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def get(self, task_id):
        with self.lock:
            return self._view(self._active(task_id))

    def _mutate(self, args, clear=False):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self._row(args.task_id)
                if clear and row is None:
                    result = {"task_id": args.task_id}
                else:
                    row = self._active(args.task_id)
                    if row["version"] != args.expected_version:
                        raise DomainError(
                            "version_conflict",
                            "任务已变化，请读取最新版本后更新。",
                            status="conflict",
                            data={"version": row["version"]},
                        )
                    if clear:
                        self.db.execute(
                            "DELETE FROM task_contexts "
                            "WHERE principal_id=? AND project_id=? AND task_id=?",
                            (*self.scope, args.task_id),
                        )
                        result = {"task_id": args.task_id}
                    else:
                        payload = json.loads(row["payload_json"])
                        for key in args.remove_fields:
                            payload["fields"].pop(key, None)
                        payload["fields"].update(self._fields(args.patch, row["expires_at"]))
                        if (set(args.patch) | set(args.remove_fields)) & {
                            "target",
                            "action",
                            "time_range",
                            "filters",
                        }:
                            payload["fields"].pop("last_result", None)
                        self.db.execute(
                            "UPDATE task_contexts "
                            "SET payload_json=?,version=version+1,updated_at=? "
                            "WHERE principal_id=? AND project_id=? AND task_id=? AND version=?",
                            (
                                self._encode(payload),
                                self.now(),
                                *self.scope,
                                args.task_id,
                                args.expected_version,
                            ),
                        )
                        result = self._view(self._active(args.task_id))
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def update(self, args):
        return self._mutate(args)

    def clear(self, args):
        return self._mutate(args, clear=True)

    def close(self):
        with self.lock:
            self.db.close()
