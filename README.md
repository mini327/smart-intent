# Smart Intent

为 AI 智能体补充显式任务上下文、对象消歧、参数校验和有来源的只读记录查询。保留宿主的语义理解能力，通过 Skill 选择何时澄清及调用工具，通过本地 MCP 服务执行确定性操作。

当前为 **0.1.0 开发预览版**。不执行消息发送、补发或业务配置修改；不是通用聊天应用，也不会自动拦截所有用户输入。

## 已实现

- 任务上下文创建、读取、字段更新、清除、有效期和版本冲突检测。
- 单用户、单项目启动范围；工具参数不能切换身份或数据目录。
- JSONL 真实文件读取、严格字段检查、同名目标消歧。
- 含时区的时间过滤、明确集合范围、状态及分类筛选。
- 基于快照与签名游标的分页；对完整筛选集合进行计数排名。
- 结构化结果、行号来源、测试数据标识和明确的错误状态。
- 官方 MCP SDK stdio 服务、锁定依赖及子进程协议测试。

## 快速启动

需要 Python 3.11+、Git 和 [uv](https://docs.astral.sh/uv/)。当前本机验证环境为 Windows、Python 3.14.0、MCP SDK 2.3.0、uv 0.13.1；其他环境的验证结果见 [兼容与测试记录](docs/验证记录.md)。

```powershell
git clone https://github.com/mini327/smart-intent.git
cd smart-intent
uv sync --frozen
uv run --frozen python scripts/make_demo_config.py
```

最后一条命令生成 `config/demo.local.toml`，输出其绝对路径。示例源为明确标记的测试数据，不代表真实业务。

```powershell
uv run --frozen python -m smart_intent --config "生成的配置绝对路径" --check-config
uv run --frozen python -m smart_intent --config "生成的配置绝对路径"
```

第二条命令启动 stdio MCP 服务并等待宿主连接，不是交互式命令行聊天。stdout 仅用于协议；日志写入 stderr。`--check-config` 只检查配置，不表示已读通数据或已连接宿主。

## 接入智能体

使用 [Qoder MCP 配置示例](integrations/qoder/mcp.example.json)，替换解释器和配置绝对路径；将服务安装到对应虚拟环境后，由宿主使用 `python -m smart_intent --config ...` 启动。Windows 解释器通常位于 `.venv/Scripts/python.exe`，Linux 为 `.venv/bin/python`。

Skill 单独按宿主支持的方式加载 [SKILL.md](SKILL.md)。加载 Skill 不会自动安装 MCP，安装 MCP 也不保证模型每次调用工具。保留宿主正常权限机制，勿开启全局自动批准来完成接入。

目前已验证官方 SDK 客户端到真实服务子进程的工具调用；尚未将服务写入用户 Qoder、Codex 或其他智能体的本地配置，不宣称已经完成这些宿主的端到端认证。

## 工具

| 名称 | 用途 | 副作用 |
|---|---|---|
| create_task_context | 创建显式任务句柄 | 写入任务存储 |
| get_task_context | 读取当前范围有效字段及版本 | 无业务写入 |
| update_task_context | 按预期版本替换或移除字段 | 写入任务存储 |
| clear_task_context | 按版本清除任务 | 删除任务存储内容 |
| resolve_target | 从记录中返回唯一目标或候选 | 只读源文件 |
| validate_task_plan | 校验并规范化查询计划 | 只读源文件 |
| query_records | 重新校验后查询或聚合 | 只读源文件 |

工具采用扁平参数，格式由 `tools/list` 返回的 schema 定义。以下为 `query_records` 的参数示例：

```json
{
  "plan": {
    "source_id": "demo-events",
    "action": "query",
    "target": {"mode": "all"},
    "time": {"expression": "2026年11月", "timezone": "Asia/Shanghai"},
    "filters": {"status": ["failed"]}
  },
  "page_size": 50
}
```

`target.mode` 必须明确为 `single`、`explicit_set` 或 `all`。单一目标与集合需指定 `target_ids`；未知 ID 不自动退化为全量查询。名称需要消歧时先调用 `resolve_target`。

聚合使用 `action=aggregate` 与 `aggregation={"count_by":"category","top_k":5}`，支持 category 或 status 分组。按次数倒序，并按分组键处理并列。插件不自动判定哪些状态代表故障，调用者须明确过滤。

分页使用上一页 `data.normalized_plan` 及 `data.next_cursor`，固定绝对时间和源快照。历史游标在快照仍有效且绑定条件匹配时可复用；客户端需缓存首屏才能回到原快照首屏。游标失效后应明确结束旧分页再重新查询。`complete` 仅指选定快照内的匹配结果已取完，不能证明外部全部业务系统的覆盖情况。

可运行的 MCP 分页示例：

```powershell
uv run --frozen python -X utf8 scripts/paginate_records.py --config config/demo.local.toml --source demo-events --page-size 2
```

输入 `n` 下一页、`p` 上一页、`q` 退出。示例保存历史页及首屏，显示来源标识；详见[分页与运行边界](docs/分页与运行边界.md)。

## 真实数据配置

复制 [配置示例](config/smart-intent.example.toml) 为忽略提交的 `config/local.toml`，设置授权项目目录、源文件及字段映射。工具不接受任意路径，也不自行创建或下载业务数据。

第一版严格要求每行都有七个字符串字段：`id`、`target_id`、`target_name`、`occurred_at`、`status`、`category`、`summary`；字段名可通过配置映射。`id` 在源中唯一，时间必须含偏移。缺字段、坏行、重复 ID 或无时区时间会导致本次查询失败，不静默跳过。

默认上限为单源20 MiB、10万行、缓存估计50 MiB、查询10秒、结果256 KiB。source_id 必须来自配置；不支持未知过滤、任意 SQL 或 shell。超大源建议先由数据生产侧按项目和时间划分。

支持今天、昨天、本周、上周、本月、上月、`YYYY-MM-DD` 和 `YYYY年M月`；缺少年份的月份需要澄清。今天、本周和本月截至查询基准时刻，昨天及上个周期按当地日历完整计算。起止时间为左闭右开区间。

默认上下文仅存在于服务内存，重启失效。持久化须通过可信配置显式启用并指定本地数据库绝对路径；任务字段不保存完整对话，历史记忆不能作为当前业务状态证明。

## 测试与构建

```powershell
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
uv run --frozen pytest -q
uv build
```

测试覆盖实际文件读取、SQLite 并发、时间边界、完整聚合、快照分页、输出上限和 MCP 子进程。Windows 未允许创建符号链接时相关真实链接测试会明确跳过，不代表该平台安全用例全部通过。GitHub CI 配置包含 Windows 与 Linux 的 Python 3.11/3.14 检查。

wheel 包用于安装服务；Skill、配置样例与接入说明在仓库或源码包中。未设置自动 PyPI 发布。

## 限制与下一步

- 当前仅支持 Windows 与具备 `/proc/self/fd` 的 Linux 文件句柄校验；macOS 暂未支持数据源读取。
- MCP 进程不等于聊天会话。无法取得可信聊天标识时，仅支持显式任务衔接；同一系统账号可修改配置的程序不在插件独立防御范围内。
- 不提供多用户远程认证，不读取完整聊天历史，不提供跨任务搜索或自动恢复。
- 目录必须由可信用户控制；链接检查不能替代操作系统隔离。持久库目录的访问权限由部署方配置。
- 本地自动化测试不代表“减少20%追问”等产品效果已经验证。30条固定任务、每条3次的真实宿主基线实验仍待进行。

## 文档

- [产品需求](docs/SmartIntent_产品需求文档.md)
- [实现方案](docs/SmartIntent_实现方案.md)
- [Word 产品需求](docs/SmartIntent_产品需求文档.docx)
- [验证与兼容记录](docs/验证记录.md)
- [分页示例、缓存与超时边界](docs/分页与运行边界.md)
- [宿主效果评测方法](evals/README.md)
