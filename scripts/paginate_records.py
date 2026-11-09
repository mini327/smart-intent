"""Interactive stdio MCP pagination example; caches displayed pages in memory."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def browse(config: Path, plan: dict, page_size: int):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "smart_intent", "--config", str(config)],
        cwd=Path(__file__).resolve().parents[1],
    )
    async with Client(parameters, read_timeout_seconds=120) as client:
        pages = []
        position = -1
        while True:
            if position == len(pages) - 1:
                arguments = {"plan": plan, "page_size": page_size}
                if pages:
                    arguments.update(
                        plan=pages[-1]["normalized_plan"], cursor=pages[-1]["next_cursor"]
                    )
                result = await client.call_tool("query_records", arguments)
                envelope = result.structured_content
                if (
                    result.is_error
                    or not envelope
                    or envelope["status"] not in ("succeeded", "no_records")
                ):
                    print(json.dumps(envelope, ensure_ascii=False, indent=2), file=sys.stderr)
                    print("查询停止；检查错误后重新运行以创建新快照。", file=sys.stderr)
                    return 1
                pages.append(envelope["data"])
                print(
                    json.dumps(
                        {"sources": envelope["sources"], "warnings": envelope["warnings"]},
                        ensure_ascii=False,
                    )
                )
                position += 1
            while True:
                page = pages[position]
                print(f"Page {position + 1} | snapshot={page['snapshot_id']}")
                print(json.dumps(page["records"], ensure_ascii=False, indent=2))
                print("n=下一页 p=上一页 q=退出（历史页含首屏来自客户端缓存）")
                command = await asyncio.to_thread(sys.stdin.readline)
                command = command.strip().lower() if command else "q"
                if command == "q":
                    return 0
                if command == "p":
                    if position:
                        position -= 1
                    else:
                        print("已经是首屏。")
                elif command == "n":
                    if position + 1 < len(pages):
                        position += 1
                    elif page["complete"]:
                        print("已经是末页。")
                    else:
                        break
                else:
                    print("请输入 n、p 或 q。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", required=True, help="配置中的授权 source_id")
    parser.add_argument("--page-size", type=int, default=2, choices=range(1, 201))
    args = parser.parse_args()
    # Explicit all-target demonstration. No task context or business writes.
    plan = {"source_id": args.source, "action": "query", "target": {"mode": "all"}}
    try:
        return asyncio.run(browse(args.config.resolve(), plan, args.page_size))
    except (KeyboardInterrupt, BrokenPipeError):
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
