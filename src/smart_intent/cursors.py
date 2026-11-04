import base64
import hashlib
import hmac
import json
import secrets

from .errors import DomainError


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


class Cursors:
    def __init__(self, config):
        self.secret = secrets.token_bytes(32)
        self.scope = digest([config.principal_id, config.project_id])

    def encode(self, snapshot_id, plan, offset, task_id):
        payload = json.dumps(
            {
                "snapshot": snapshot_id,
                "plan": digest(plan),
                "offset": offset,
                "scope": self.scope,
                "task_id": task_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.digest(self.secret, payload, "sha256")
        return base64.urlsafe_b64encode(signature + payload).decode()

    def decode(self, token, plan, task_id):
        try:
            raw = base64.b64decode(token, altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self.secret, payload, "sha256")):
                raise ValueError("signature")
            data = json.loads(payload)
            if data["scope"] != self.scope or data["task_id"] != task_id:
                raise ValueError("scope")
            if type(data["offset"]) is not int or data["offset"] < 0:
                raise ValueError("offset")
        except (ValueError, KeyError, TypeError) as exc:
            raise DomainError("cursor_invalid", "游标无效或不属于当前任务。") from exc
        if data["plan"] != digest(plan):
            raise DomainError("cursor_query_mismatch", "请沿用上一页返回的规范化计划与相同任务。")
        return data
