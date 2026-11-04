"""Thin MCP SDK adapter with exact, flat Pydantic-generated input schemas."""

import asyncio

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .application import INPUTS, Application
from .models import Envelope

DESCRIPTIONS = {
    "create_task_context": "为需要后续衔接的任务创建临时上下文；不代表聊天会话或业务授权。",
    "get_task_context": "读取当前项目的有效任务字段和版本；未知或过期任务不会恢复。",
    "update_task_context": "按预期版本更新明确字段；冲突后先重读，不能覆盖旧版本。",
    "clear_task_context": "按预期版本清除当前任务；不存在时幂等成功。",
    "resolve_target": "从授权JSONL源查找对象；多个候选必须消歧，源记录不是当前身份认证。",
    "validate_task_plan": "检查目标、时间、动作和条件；返回规范化计划，不表示授权或已执行。",
    "query_records": "重检完整计划并只读查询或聚合；分页沿用规范化绝对时间及游标。",
}


def make_server(app: Application) -> Server:
    schemas = {name: model.model_json_schema() for name, model in INPUTS.items()}

    async def list_tools(_ctx, _params):
        tools = []
        for name, schema in schemas.items():
            readonly = name in {
                "get_task_context",
                "resolve_target",
                "validate_task_plan",
                "query_records",
            }
            tools.append(
                types.Tool(
                    name=name,
                    description=DESCRIPTIONS[name],
                    input_schema=schema,
                    output_schema=Envelope.model_json_schema(),
                    annotations=types.ToolAnnotations(
                        read_only_hint=readonly,
                        destructive_hint=name == "clear_task_context",
                        idempotent_hint=readonly or name == "clear_task_context",
                        open_world_hint=False,
                    ),
                )
            )
        return types.ListToolsResult(tools=tools)

    async def call_tool(_ctx, params):
        result = await asyncio.to_thread(app.call, params.name, params.arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(text=result.model_dump_json())],
            structured_content=result.model_dump(mode="json"),
            is_error=result.status in {"failed", "blocked", "rejected", "not_found", "expired"},
        )

    return Server(
        "smart-intent",
        version=__version__,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: schemas.get(name),
    )


async def serve(app: Application):
    server = make_server(app)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
