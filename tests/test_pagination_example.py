import asyncio
import json
import sys
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_client_example_navigates_cached_first_page(factory, tmp_path):
    _, source = factory()
    quote = lambda value: json.dumps(str(value).replace("\\", "/"))  # noqa: E731
    config = tmp_path / "example.toml"
    config.write_text(
        'principal_id="local"\nproject_id="project"\n'
        f'project_root={quote(source.parent)}\n[[sources]]\nid="events"\n'
        f'path={quote(source)}\nallowed_root={quote(source.parent)}\nclassification="test"\n',
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-X",
        "utf8",
        str(root / "scripts" / "paginate_records.py"),
        "--config",
        str(config),
        "--source",
        "events",
        "--page-size",
        "2",
        cwd=root,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(b"n\np\nn\nn\nq\n"), 30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode("utf-8")
    output = stdout.decode("utf-8")
    headers = [line for line in output.splitlines() if line.startswith("Page ")]
    assert [line.split(" | ")[0] for line in headers] == [
        "Page 1",
        "Page 2",
        "Page 1",
        "Page 2",
        "Page 2",
    ]
    assert len({line.split("snapshot=")[1] for line in headers}) == 1
    assert "已经是末页" in output
    assert '"classification": "test"' in output
