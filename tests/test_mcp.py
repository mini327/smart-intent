import json
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters


@pytest.mark.asyncio
async def test_real_stdio_tools_and_unicode_contract(factory, tmp_path, plan):
    app, source = factory()
    cfg_path = tmp_path / "stdio.toml"

    # TOML strings use JSON-compatible quoting for these concrete path values.
    def quote(value):
        return json.dumps(str(value), ensure_ascii=False)

    cfg_path.write_text(
        'principal_id="local"\nproject_id="project"\n'
        f'project_root={quote(source.parent)}\n[[sources]]\nid="events"\n'
        f'path={quote(source)}\nallowed_root={quote(source.parent)}\nclassification="test"\n',
        encoding="utf-8",
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "smart_intent", "--config", str(cfg_path)],
        cwd=Path(__file__).resolve().parents[1],
    )
    async with Client(parameters, read_timeout_seconds=15) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 7
        schemas = {tool.name: tool for tool in tools}
        assert schemas["query_records"].input_schema["additionalProperties"] is False
        assert schemas["query_records"].output_schema is not None
        assert not schemas["update_task_context"].annotations.read_only_hint
        created = await client.call_tool("create_task_context", {})
        assert not created.is_error
        task = created.structured_content["data"]["task_id"]
        result = await client.call_tool("query_records", {"plan": plan, "task_id": task})
        assert not result.is_error
        data = result.structured_content
        assert data["status"] == "succeeded"
        assert data["data"]["records"][0]["target_name"] == "同名"
        assert json.loads(result.content[0].text) == data
        rejected = await client.call_tool("query_records", {"plan": {**plan, "action": "补发"}})
        assert rejected.is_error
        assert rejected.structured_content["error"]["code"] == "action_not_supported"
        extra = await client.call_tool(
            "get_task_context", {"task_id": task, "principal_id": "other"}
        )
        assert extra.is_error
