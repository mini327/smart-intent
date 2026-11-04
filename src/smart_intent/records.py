import hashlib
import json
import sys
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

from .errors import DomainError
from .paths import read_source
from .time_ranges import aware


def strict_json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value

    def constant(_):
        raise ValueError("Non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


@dataclass(frozen=True)
class Record:
    data: dict
    timestamp: float
    line: int


@dataclass(frozen=True)
class Snapshot:
    id: str
    source_id: str
    records: tuple[Record, ...]
    content_hash: str
    captured_at: str
    source_mtime: float
    expires_at: float
    weight: int


def memory_weight(value, check):
    seen = set()

    def visit(item):
        check()
        ident = id(item)
        if ident in seen:
            return 0
        seen.add(ident)
        size = sys.getsizeof(item)
        if isinstance(item, dict):
            size += sum(visit(k) + visit(v) for k, v in item.items())
        elif isinstance(item, (tuple, list)):
            size += sum(visit(v) for v in item)
        elif isinstance(item, Record):
            size += visit(item.data) + visit(item.timestamp) + visit(item.line)
        return size

    return visit(value)


class RecordSource:
    def __init__(self, config, clock):
        self.config, self.clock = config, clock
        self.cache: OrderedDict[str, Snapshot] = OrderedDict()

    def source(self, source_id):
        for source in self.config.sources:
            if source.id == source_id:
                return source
        raise DomainError("access_denied", "该数据源未在当前项目授权。")

    def deadline(self):
        until = time.monotonic() + self.config.limits.query_timeout_seconds

        def check():
            if time.monotonic() > until:
                raise DomainError("query_timeout", "只读查询超时，请缩小数据源。", status="failed")

        return check

    def snapshot(self, source_id, check):
        source = self.source(source_id)
        raw, info = read_source(
            source, self.config.project_root, self.config.limits.source_bytes_max, check
        )
        try:
            lines = raw.decode("utf-8-sig").splitlines()
        except UnicodeDecodeError as exc:
            raise DomainError(
                "source_schema_error", "数据源必须为UTF-8 JSONL。", status="failed"
            ) from exc
        if len(lines) > self.config.limits.source_records_max:
            raise DomainError("source_too_large", "数据源超过记录数限制。")
        records, ids = [], set()
        mapping = source.fields.model_dump()
        for number, line in enumerate(lines, 1):
            check()
            try:
                row = strict_json(line)
                if not isinstance(row, dict):
                    raise ValueError("Expected object")
                values = {key: row[column] for key, column in mapping.items()}
                if any(not isinstance(v, str) for v in values.values()):
                    raise ValueError("Fields must be strings")
                if any(
                    not values[k].strip() or len(values[k]) > 200
                    for k in ("record_id", "target_id", "target_name", "status", "category")
                ):
                    raise ValueError("Invalid field length")
                if len(values["timestamp"]) > 100 or len(values["summary"]) > 100000:
                    raise ValueError("Field too large")
                if values["record_id"] in ids:
                    raise ValueError("Duplicate record ID")
                stamp = aware(datetime.fromisoformat(values.pop("timestamp")))
                values["occurred_at"] = stamp.isoformat()
                values["summary_truncated"] = len(values["summary"]) > 4000
                values["summary"] = values["summary"][:4000]
                values["source"] = {"source_id": source_id, "line": number}
                records.append(Record(values, stamp.timestamp(), number))
                ids.add(values["record_id"])
            except (ValueError, TypeError, KeyError, DomainError, RecursionError) as exc:
                raise DomainError(
                    "source_schema_error",
                    "源记录字段或时间无效。",
                    status="failed",
                    fields={"line": str(number)},
                ) from exc
        frozen = tuple(records)
        weight = memory_weight(frozen, check)
        if weight > self.config.limits.snapshot_cache_bytes_max:
            raise DomainError("snapshot_too_large", "解析后的快照超过内存缓存限制。")
        self.expire()
        while (
            self.cache
            and sum(s.weight for s in self.cache.values()) + weight
            > self.config.limits.snapshot_cache_bytes_max
        ):
            self.cache.popitem(last=False)
        snapshot = Snapshot(
            uuid.uuid4().hex,
            source_id,
            frozen,
            hashlib.sha256(raw).hexdigest(),
            aware(self.clock()).isoformat(),
            info.st_mtime,
            time.monotonic() + self.config.limits.snapshot_ttl_seconds,
            weight,
        )
        self.cache[snapshot.id] = snapshot
        return snapshot

    def expire(self):
        for key in list(self.cache):
            if self.cache[key].expires_at <= time.monotonic():
                del self.cache[key]

    def cached(self, snapshot_id, source_id):
        self.source(source_id)
        self.expire()
        snapshot = self.cache.get(snapshot_id)
        if snapshot is None:
            raise DomainError("cursor_expired", "快照已过期或服务重启，请从第一页重新查询。")
        if snapshot.source_id != source_id:
            raise DomainError("cursor_invalid", "游标与当前数据源不一致。")
        self.cache.move_to_end(snapshot_id)
        return snapshot

    def evidence(self, snapshot):
        source = self.source(snapshot.source_id)
        return {
            "source_id": source.id,
            "classification": source.classification,
            "snapshot_id": snapshot.id,
            "content_sha256": snapshot.content_hash,
            "captured_at": snapshot.captured_at,
            "source_mtime": snapshot.source_mtime,
            "record_count": len(snapshot.records),
        }

    def resolve(self, snapshot, query, limit, check):
        targets = {}
        for record in snapshot.records:
            check()
            item = record.data
            target = targets.setdefault(
                item["target_id"],
                {"target_id": item["target_id"], "names": set(), "source": item["source"]},
            )
            target["names"].add(item["target_name"])
        term = query.strip().casefold()
        matches = [t for t in targets.values() if t["target_id"] == query]
        if not matches:
            matches = [t for t in targets.values() if term in {n.casefold() for n in t["names"]}]
        if not matches:
            matches = [t for t in targets.values() if any(term in n.casefold() for n in t["names"])]
        matches.sort(key=lambda t: t["target_id"])
        candidates = [{**t, "names": sorted(t["names"])} for t in matches[:limit]]
        return {
            "candidates": candidates,
            "candidate_count": len(matches),
            "truncated": len(matches) > limit,
            "snapshot_id": snapshot.id,
        }
