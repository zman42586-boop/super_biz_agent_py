# SuperBizAgent

> 企业级智能对话与 **OnCall / AIOps** 助手：RAG 知识库问答、**主动告警 Webhook**、**Plan–Execute–Replanner** 诊断流、**MCP** 监控工具、**Hot/Cold 记忆**落盘与 **Markdown Skill** 注入 Planner。

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-latest-orange.svg)](https://www.langchain.com/)

## ✨ 核心特性

- 🤖 **智能对话** — LangChain 多轮对话 + 流式输出（RAG Agent 状态图，历史过长时 Snip 截断）
- 📚 **RAG 问答** — 文档切分、向量入库（Milvus）、`retrieve_knowledge` 工具按需检索（Agentic RAG）
- 🔧 **AIOps 诊断** — LangGraph：`planner → executor → microcompact → replanner` 循环，支持大工具结果落盘与步骤折叠
- 🔔 **主动 OnCall（可选）** — `scripts/lhm_alert_agent.py` 轮询 LibreHardwareMonitor，超阈经 `POST /api/alerts/ingest` 上报；邮件通知；`critical` 可触发自动诊断
- 🧠 **分层记忆** — `memory/MEMORY.md` 热索引 + `memory/incidents/` 完整报告 + `memory/artifacts/` Microcompact 原文
- 📋 **Skill** — `.claude/skills/*.md`（frontmatter + 排查步骤），经 `app/claude_skills/reader.py` 注入 Planner
- 🌐 **Web 界面** — 静态页：快速问答 / 流式对话 / 智能运维
- 🔌 **MCP** — 当前主进程默认接入 **Monitor MCP**（`streamable-http`）；Makefile 另提供 **CLS MCP** 启动目标（需自行接入客户端配置）

## 🛠️ 技术栈

| 类别 | 选型 |
|------|------|
| API / 运行时 | FastAPI、Uvicorn、Python **3.11+**（见 `pyproject.toml`） |
| Agent / 编排 | LangChain、**LangGraph**（StateGraph、条件边、MemorySaver checkpoint） |
| LLM | **OpenAI 兼容 API**（`langchain-openai`），通过 `DASHSCOPE_API_BASE` + `DASHSCOPE_API_KEY` 配置；默认可对接 DeepSeek、阿里云 DashScope、OpenAI 等 |
| Embedding | HuggingFace / `sentence-transformers`（如 `BAAI/bge-small-zh-v1.5`，见配置项） |
| 向量库 | Milvus（`langchain-milvus`、`pymilvus`） |
| 工具协议 | MCP（`langchain-mcp-adapters`、`fastmcp`） |

## 🏗️ 诊断与告警架构（摘要）

```
告警轮询 (lhm_alert_agent) ──Webhook──► AlertService ──邮件──► 运维
                              │
                              └─ critical + oncall_auto_diagnosis ──► AIOps (LangGraph)
                                                                            │
RAG (Milvus) ◄── retrieve_knowledge ◄── Planner ◄── 注入 MEMORY.md / Skills
Monitor MCP ◄── 工具调用 ◄── Executor ──► Microcompact ──► Replanner ──► 报告
本地日志 ◄── search_log ◄──┘                              │
                                                          └──► memory_writer → incidents + MEMORY.md
```

## 🚀 快速开始

### 环境要求

- **Python 3.11+**（`<3.14`，与 `pyproject.toml` 一致）
- 可访问的 **LLM** 与 **Embedding** 依赖（见 `.env`）
- Docker（用于 Milvus，可选本地已有 Milvus 则改配置即可）

### Linux / macOS

```bash
git clone <repository_url>
cd super_biz_agent_py

# 依赖（推荐 uv）
pip install uv
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv sync                                  # 或: uv pip install -e .

# 在项目根目录创建 .env，按下文「配置说明」填入 LLM / Milvus 等变量

make init    # Docker + 启动 MCP + API + 上传 aiops-docs（依 Makefile 定义）
# 或分步：make up && make start-monitor && make start-api && make upload
```

### Windows（推荐 `start-windows.bat`）

1. 安装依赖：`uv sync` 或 `pip install -e .`
2. 配置 `.env`
3. 启动 Docker Desktop 后执行：

```powershell
.\start-windows.bat
```

脚本会依次：**Milvus** → **LibreHardwareMonitor**（若存在 `LibreHardwareMonitor\LibreHardwareMonitor.exe`）→ **Monitor MCP (8004)** → **FastAPI (9900)** → 上传 `aiops-docs\*.md` → **LHM Alert Agent**（主动温度告警）。

停止：`.\stop-windows.bat`

> **说明**：当前 `app/config.py` 中 MCP 仅注册 **monitor**；若需 **CLS 日志 MCP**，可执行 `make start-cls`（Linux/macOS）并自行扩展 `config.mcp_servers` 与客户端加载逻辑。

### 访问

| 用途 | 地址 |
|------|------|
| Web | http://localhost:9900 |
| OpenAPI | http://localhost:9900/docs |
| 健康检查 | http://localhost:9900/health |
| Monitor MCP | http://localhost:8004/mcp |

## 📡 API 接口

| 功能 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 健康检查 | GET | `/health` | 服务与 Milvus 状态（**无** `/api` 前缀） |
| 普通对话 | POST | `/api/chat` | 非流式 |
| 流式对话 | POST | `/api/chat_stream` | SSE |
| 清空会话 | POST | `/api/chat/clear` | 按会话清理 |
| AIOps 诊断 | POST | `/api/aiops` | SSE，Plan–Execute–Replanner |
| 上传并索引 | POST | `/api/upload` | 支持 `txt` / `md`，见 `file.py` |
| 告警上报 | POST | `/api/alerts/ingest` | Bearer `ALERT_WEBHOOK_TOKEN` |
| 活跃告警 | GET | `/api/alerts/active` | 内存中的 active 列表 |
| 解决告警 | POST | `/api/alerts/{alert_id}/resolve` | 标记 resolved |

### 调用示例

```bash
curl -s http://localhost:9900/health

curl -X POST "http://localhost:9900/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"你好"}'

curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"session-123"}' \
  --no-buffer
```

## 📁 项目结构（与代码同步）

```
super_biz_agent_py/
├── app/
│   ├── main.py                 # FastAPI 入口、路由挂载、Milvus 生命周期
│   ├── config.py               # Pydantic Settings（LLM/Milvus/RAG/MCP/SMTP/告警/LHM）
│   ├── api/
│   │   ├── chat.py             # /api/chat, chat_stream, chat/clear
│   │   ├── aiops.py            # /api/aiops（SSE）
│   │   ├── alerts.py           # /api/alerts/*（Webhook、列表、resolve）
│   │   ├── file.py             # /api/upload
│   │   └── health.py           # /health
│   ├── services/
│   │   ├── rag_agent_service.py
│   │   ├── aiops_service.py    # LangGraph 编译与 execute 流
│   │   ├── alert_service.py
│   │   ├── mail_service.py
│   │   ├── vector_*.py, document_splitter_service.py
│   ├── agent/
│   │   ├── mcp_client.py       # MultiServerMCPClient、重试拦截器
│   │   └── aiops/
│   │       ├── planner.py, executor.py, replanner.py
│   │       ├── microcompact.py # 大结果落盘 + 摘要写回 past_steps
│   │       └── state.py        # PlanExecuteState、past_steps reducer
│   ├── claude_skills/reader.py # 加载 .claude/skills/*.md → Planner 文本
│   ├── memory/
│   │   ├── memory_writer.py    # incidents + MEMORY.md
│   │   └── memory_reader.py    # 加载热记忆给 Agent
│   ├── tools/                  # get_current_time, retrieve_knowledge, search_log
│   ├── models/
│   ├── core/                   # llm_factory, milvus_client
│   └── utils/                  # logger, token_meter
├── .claude/skills/             # Markdown Skill（id/name/description + 正文）
├── memory/                     # 运行期生成：MEMORY.md, incidents/, artifacts/
├── mcp_servers/
│   ├── monitor_server.py       # CPU/内存/LHM 等（默认接入）
│   ├── cls_server.py           # 可选：CLS 日志 MCP
│   └── README.md
├── scripts/
│   └── lhm_alert_agent.py      # 主动轮询 + Webhook 上报
├── static/                     # 前端静态资源
├── aiops-docs/                 # 默认运维知识 Markdown
├── vector-database.yml         # Milvus Compose
├── start-windows.bat / stop-windows.bat
├── Makefile
├── pyproject.toml
└── README.md
```

## ⚙️ 配置说明（`.env`）

变量名与 `app/config.py` 对应（不区分大小写）。以下为常用项：

```bash
# ── LLM（OpenAI 兼容）────────────────────────────────────
# 环境变量名沿用 DASHSCOPE_*，可指向任意兼容端点
DASHSCOPE_API_KEY=
DASHSCOPE_API_BASE=https://api.deepseek.com/v1
# 通义示例: https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=deepseek-chat
DASHSCOPE_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5

# ── Milvus ───────────────────────────────────────────────
MILVUS_HOST=localhost
MILVUS_PORT=19530

# ── RAG / 分块 ─────────────────────────────────────────
RAG_TOP_K=3
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100

# ── MCP Monitor ─────────────────────────────────────────
MCP_MONITOR_TRANSPORT=streamable-http
MCP_MONITOR_URL=http://localhost:8004/mcp

# ── 告警 Webhook（ingest 接口 Bearer）──────────────────
ALERT_WEBHOOK_TOKEN=

# ── SMTP（告警/诊断邮件）────────────────────────────────
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
SMTP_TO=

# ── LHM / OnCall 规则（与 scripts/lhm_alert_agent.py 对齐）─
LHM_BASE_URL=http://127.0.0.1:8085
LHM_SENSOR_NAME_CONTAINS=CCDs Max (Tdie)
ONCALL_TEMP_THRESHOLD_C=50
ONCALL_TEMP_DURATION_SEC=60
ONCALL_TEMP_COOLDOWN_SEC=120
ONCALL_AUTO_DIAGNOSIS=true
```

## 🎯 AIOps 智能运维

基于 **Plan–Execute–Replanner**，LangGraph 节点顺序为：

1. **Planner** — 结构化计划（如 `Plan.steps`）；可加载 `MEMORY.md` 与 Skill 描述；按需 `retrieve_knowledge`
2. **Executor** — 执行单步，调用本地工具 + MCP；更新 `past_steps`（reducer 为**全量替换**，以配合压缩）
3. **Microcompact** — 超大工具结果写入 `memory/artifacts/{session}/`，上下文替换为短摘要 + 路径
4. **Replanner** — 决定继续执行 / 结束并生成 `response`；含历史步骤折叠（Collapse）与防死循环上限

诊断结束后 **`memory_writer`** 写入 `memory/incidents/` 并更新 **`memory/MEMORY.md`** 索引。

## 🔔 主动 OnCall（温度示例）

1. 运行 **LibreHardwareMonitor**，开启 **Remote Web Server**（默认 8085）
2. 启动本服务与 **`scripts/lhm_alert_agent.py`**（`start-windows.bat` 已包含）
3. Agent 连续超阈达到 `ONCALL_TEMP_DURATION_SEC` 后，携带 Bearer 调用 **`POST /api/alerts/ingest`**
4. **AlertService** 去重、发邮件；`severity=critical` 且 `ONCALL_AUTO_DIAGNOSIS=true` 时后台触发 AIOps，并可在流程中发送诊断邮件

## 📋 Skill 与记忆

- **Skill**：在 `.claude/skills/` 新增 `*.md`，顶部 YAML 含 `id`、`name`、`description`；重启后由 reader 注入 Planner。
- **Hot**：`memory/MEMORY.md` — 最近 incident 表格索引  
- **Cold**：`memory/incidents/*.md` — 完整报告；`memory/artifacts/` — 工具原文证据  

可选：在 `memory/topics/` 下维护服务画像等（需业务自行约定加载方式）。

## 📝 开发指南

```bash
make help           # 查看全部目标
make init / start / stop / restart
make test / lint / format
make start-monitor / start-cls   # MCP 分项启动（Linux/macOS）
```

## 🐛 常见问题

- **Windows 无 make**：使用 `start-windows.bat` / `stop-windows.bat`，或按 Makefile 内命令手工执行。
- **健康检查 404**：应请求 **`GET /health`**，不是 `/api/health`。
- **MCP 连不上**：确认 `monitor_server.py` 已监听 **8004**，且 `MCP_MONITOR_URL` 一致。
- **Milvus**：`docker compose -f vector-database.yml ps`，必要时 `restart` standalone。
- **日志**：主服务 `logs/app_YYYY-MM-DD.log`；MCP 见终端或项目内 `mcp_*.log`（若配置）。

## 📚 参考资源

- [FastAPI](https://fastapi.tiangolo.com/)
- [LangChain](https://python.langchain.com/)
- [LangGraph](https://langchain-ai.github.io/langgraph/)
- [LangGraph Plan-and-Execute](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/)
- [MCP](https://modelcontextprotocol.io/)
- [Milvus](https://milvus.io/)

## 📄 许可证

author： chief  

MIT License
