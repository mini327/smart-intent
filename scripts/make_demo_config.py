"""Generate a local absolute-path config for the explicitly labelled test data."""

import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent


def quote(path):
    return json.dumps(str(path).replace("\\", "/"), ensure_ascii=False)


destination = root / "config" / "demo.local.toml"
if destination.exists():
    raise SystemExit("Demo config already exists; remove or edit it explicitly.")
destination.write_text(
    'schema_version = 1\nprincipal_id = "local-demo"\nproject_id = "smart-intent-demo"\n'
    f'project_root = {quote(root)}\ntimezone = "Asia/Shanghai"\n\n'
    '[context]\nmode = "ephemeral"\n\n[[sources]]\nid = "demo-events"\n'
    f"path = {quote(root / 'examples' / 'events.jsonl')}\n"
    f'allowed_root = {quote(root / "examples")}\nclassification = "test"\n',
    encoding="utf-8",
)
print(destination)
