# SuperBizAgent

> 企业级 OnCall 智能运维平台 — RAG 知识库问答 + AIOps 自动诊断 + 主动告警 + 心跳监控 + MCP 多工具集成

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-latest-orange.svg)](https://www.langchain.com/)

## 项目概述

SuperBizAgent 是一个面向运维场景的智能助手系统，提供三大核心能力：

1. **RAG 智能对话** — 基于 Milvus 向量数据库的检索增强生成，支持多轮对话和流式输出
2. **AIOps 自动诊断** — LangGraph Plan-Execute-Replanner 工作流，自动分析告警根因并生成诊断报告
3. **主动 OnCall 告警** — Agent 进程轮询硬件温度 + 进程存活监控 + 崩溃日志检测，Webhook 上报，邮件通知
4. **Agent 评估体系** — 8 场景 ground truth + Recall@K/Precision@K/MRR + LLM-as-Judge，可回归的评测闭环

## 技术栈

| 层级 | 选型 |
|------|------|
| Web 框架 | FastAPI + Uvicorn + SSE (sse-starlette) |
| AI 编排 | LangChain + LangGraph (StateGraph, MemorySaver checkpoint) |
| LLM | OpenAI 兼容 API (langchain-openai)，默认对接 DeepSeek，可切换任意兼容厂商 |
| Embedding | HuggingFace sentence-transformers (BAAI/bge-small-zh-v1.5，本地运行) |
| 向量数据库 | Milvus 2.5 (pymilvus + langchain-milvus)，Docker Compose 部署 |
| 工具协议 | MCP (langchain-mcp-adapters + fastmcp)，本机 Monitor MCP |
| 前端 | 原生 HTML/CSS/JS，marked.js 渲染 Markdown，highlight.js 代码高亮 |
| 监控数据 | psutil (CPU/内存)，LibreHardwareMonitor (硬件温度传感器) |
| 邮件 | SMTP SSL (smtplib) |
| 包管理 | uv + pyproject.toml |

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Web UI (:9900)                           │
│             快速问答 / 流式对话 / AIOps 诊断                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    FastAPI (app/main.py)                        │
│  /api/chat  /api/chat_stream  /api/aiops  /api/alerts/ingest   │
│  /api/upload  /api/heartbeat  /health                          │
└──────┬──────────┬──────────┬──────────┬──────────────┬─────────┘
       │          │          │          │              │
┌──────▼──┐ ┌────▼────┐ ┌───▼────┐ ┌───▼──────┐ ┌─────▼──────┐
│ RAG     │ │ AIOps   │ │ Alert   │ │ Memory   │ │ MCP Client │
│ Agent   │ │ Service │ │ Service │ │ Reader/  │ │ (Multi-    │
│ (Lang-  │ │ (Lang-  │ │ (去重+  │ │ Writer   │ │  Server)   │
│ Graph)  │ │ Graph)  │ │ 邮件+   │ │          │ │            │
│         │ │         │ │ 诊断)   │ │          │ │            │
└────┬────┘ └───┬─────┘ └───┬─────┘ └────┬─────┘ └──────┬─────┘
     │          │           │             │              │
┌────▼────┐ ┌──▼──────────▼──┐ ┌───────▼───────┐ ┌─────▼─────┐
│ Milvus  │ │ Plan → Execute │ │ SMTP (163)    │ │ Monitor   │
│ (向量库) │ │ → Microcompact│ │               │ │ MCP (:8004│
│         │ │ → Replan       │ │               │ │ CPU/Mem/  │
│         │ │                │ │               │ │ LHM Temp) │
└─────────┘ └────────────────┘ └───────────────┘ └───────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Agent 进程 (scripts/lhm_alert_agent.py)                       │
│  每 5s 轮询：温度 + 进程存活 + 崩溃日志检测 → Webhook 上报       │
│  每轮心跳上报完整系统状态 → 服务端失联检测 → 死因分析             │
└─────────────────────────────────────────────────────────────────┘
```

## 项目结构

```
super_biz_agent_py/
├── app/
│   ├── main.py                     # FastAPI 入口，路由挂载，Milvus 生命周期
│   ├── config.py                   # Pydantic Settings（LLM/Milvus/RAG/MCP/SMTP/告警）
│   ├── api/
│   │   ├── chat.py                 # /api/chat, chat_stream, chat/clear
│   │   ├── aiops.py                # /api/aiops (SSE 流式诊断)
│   │   ├── alerts.py               # /api/alerts/ingest, active, resolve + /api/heartbeat
│   │   ├── file.py                 # /api/upload（文件上传 + 自动入库）
│   │   └── health.py               # /health（服务 + Milvus 状态）
│   ├── services/
│   │   ├── rag_agent_service.py    # RAG Agent（LangGraph create_agent + Snip 截断）
│   │   ├── aiops_service.py        # Plan-Execute-Replanner 工作流编译与执行
│   │   ├── alert_service.py        # 告警去重 + 邮件 + 触发诊断 + 心跳监控 + 死因分析
│   │   ├── mail_service.py         # SMTP SSL 邮件发送
│   │   ├── vector_embedding_service.py  # HuggingFace 本地 Embedding
│   │   ├── vector_store_manager.py      # langchain-milvus VectorStore 封装
│   │   ├── vector_index_service.py      # 文件读取 → 分割 → 入库
│   │   ├── vector_search_service.py     # Milvus 向量检索
│   │   └── document_splitter_service.py # Markdown 标题分割 + 递归字符分割
│   ├── agent/
│   │   ├── mcp_client.py           # MultiServerMCPClient + 重试拦截器
│   │   └── aiops/
│   │       ├── state.py            # PlanExecuteState (含全量替换 reducer)
│   │       ├── planner.py          # 制定诊断计划（RAG + 长期记忆 + Skill 注入）
│   │       ├── executor.py         # 执行单步（LLM 决策工具调用 → ToolNode 执行）
│   │       ├── microcompact.py     # 大工具结果落盘 artifacts/ + 摘要写回
│   │       ├── replanner.py        # 决策 continue/replan/respond + Collapse 折叠
│   │       └── utils.py            # format_tools_description
│   ├── claude_skills/
│   │   ├── __init__.py
│   │   └── reader.py               # 解析 .claude/skills/*.md (YAML frontmatter + 正文)
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── memory_reader.py        # 加载 MEMORY.md Hot 记忆注入 Prompt
│   │   └── memory_writer.py        # 诊断报告写入 incidents/ + 更新 MEMORY.md 索引
│   ├── tools/
│   │   ├── knowledge_tool.py       # retrieve_knowledge (Milvus 向量检索)
│   │   ├── time_tool.py            # get_current_time
│   │   └── log_tool.py             # search_log (本地 app_*.log 检索)
│   ├── models/
│   │   ├── request.py              # ChatRequest, ClearRequest
│   │   ├── response.py             # ChatResponse, SessionInfoResponse, ApiResponse
│   │   ├── alert.py                # AlertIngestRequest, AlertRecord, AlertEvidence
│   │   └── aiops.py                # AIOpsRequest, DiagnosisResponse
│   ├── core/
│   │   ├── llm_factory.py          # ChatOpenAI 工厂（支持多厂商切换）
│   │   └── milvus_client.py        # Milvus 连接管理 + Collection 创建/索引/维度检测
│   └── utils/
│       ├── logger.py               # Loguru 配置（控制台 + 按天轮转文件）
│       └── token_meter.py          # Token 估算 + 压缩统计日志
├── mcp_servers/
│   ├── monitor_server.py           # Monitor MCP Server (CPU/内存/LHM 温度)
│   └── README.md
├── scripts/
│   └── lhm_alert_agent.py          # OnCall 检测 Agent（温度轮询 + 进程监控 + 崩溃日志 + 心跳）
├── static/
│   ├── index.html                  # 前端 SPA
│   ├── app.js                      # 前端逻辑
│   └── styles.css                  # 样式
├── .claude/skills/                 # Claude Code 风格 Skill (Markdown + frontmatter)
├── aiops-docs/                     # 默认运维知识文档
├── oncall_knowledge_docs_50/       # OnCall 知识库 (incidents/runbooks/services/sops/middleware)
├── memory/                         # 运行时生成：MEMORY.md, incidents/, artifacts/, emergency/
├── vector-database.yml             # Milvus + etcd + MinIO + Attu Docker Compose
├── start-windows.bat               # Windows 一键启动脚本
├── stop-windows.bat                # Windows 一键停止脚本
├── Makefile                        # Linux/macOS 运维命令集
├── pyproject.toml                  # 项目配置与依赖
└── .env                            # 环境变量（LLM/Milvus/MCP/SMTP/告警阈值）
```

## AIOps 诊断流程

基于 LangGraph 的 **Plan-Execute-Replanner** 循环：

```
输入（告警/任务描述）
  │
  ▼
Planner ────────────── 制定步骤计划
  │                    ├── 从 Milvus 检索相关经验文档
  │                    ├── 加载 MEMORY.md 长期记忆
  │                    ├── 注入 .claude/skills/*.md 标准流程
  │                    └── 基于可用工具列表生成结构化计划
  ▼
Executor ───────────── 执行当前步骤
  │                    ├── LLM 决策调用哪些工具
  │                    ├── ToolNode 自动执行（本地工具 + MCP 工具）
  │                    └── 结果追加到 past_steps
  ▼
Microcompact ───────── 压缩超大门槛
  │                    ├── 工具结果 > 阈值 → 落盘 memory/artifacts/{session}/
  │                    └── 替换为 "头 + 关键错误行 + 尾" 摘要
  ▼
Replanner ──────────── 决策
  │                    ├── respond: 信息充足 → 生成最终报告
  │                    ├── continue: 计划合理 → 继续下一步
  │                    └── replan: 调整计划 → 替换剩余步骤（有限制）
  │                    ├── Collapse: 步骤过多时折叠旧步骤为摘要
  │                    └── 防死循环: MAX_STEPS=8 强制 respond
  ▼
MemoryWriter ───────── 持久化
                       ├── Cold: 完整报告 → memory/incidents/
                       ├── Hot: 索引摘要 → memory/MEMORY.md
                       └── 自动索引到 Milvus（供后续召回）
```

## 告警与心跳监控

```
Agent 进程 (lhm_alert_agent.py, 每 5s)
  │
  ├── 进程监控（优先级最高）
  │   ├── 检查 MONITOR_PROCESS 是否存活
  │   ├── 上次还在、这次消失 → 扫描崩溃日志目录
  │   ├── 解析 MATLAB crash dump（崩溃类型 + 调用栈 + 出错行号）
  │   └── POST /api/alerts/ingest（含量崩溃原因） → 立刻收到邮件
  │
  ├── 采集系统快照（每轮都做，不再只在高温时）
  │   ├── CPU% + 内存% + TOP 10 进程
  │   └── 写 light_snapshot.json 到磁盘
  │
  ├── 发送心跳 POST /api/heartbeat（每轮都发）
  │   └── 携带: 温度 + CPU% + 内存% + TOP 进程列表
  │
  ├── 读取 LibreHardwareMonitor 温度传感器
  │
  ├── 温度连续超阈 ONCALL_TEMP_DURATION_SEC → POST /api/alerts/ingest
  │   ├── warning (超阈 < 5°C) / critical (超阈 ≥ 5°C)
  │   └── 冷却期 ONCALL_TEMP_COOLDOWN_SEC 内不重复
  │
  ├── 温度 ≥ EMERGENCY_SNAPSHOT_TEMP_C → 写 last_breath.json
  │
  └── 💀 主机死机 → 心跳中断 → 重启后启动自检打印死前快照

服务端 (alert_service.py)
  │
  ├── ingest: 去重 → 发首次告警邮件 → critical + auto_diagnosis → 后台 AIOps
  │
  └── _check_heartbeats (每 30s):
      ├── 心跳超时 ONCALL_HEARTBEAT_TIMEOUT_SEC → 主机失联
      ├── 取最后心跳快照推断死因:
      │   ├── 温度走势 → 散热失效 / 过热关机
      │   ├── CPU/内存 → CPU 过载
      │   ├── TOP 进程 → 嫌疑人名单
      │   └── 综合判定 → 发送含死因分析的告警邮件
      └── 心跳恢复 → 自动 resolve 失联告警
```

## API 接口

| 功能 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 健康检查 | GET | `/health` | 服务状态 + Milvus 连接 |
| 快速对话 | POST | `/api/chat` | 非流式，完整返回 |
| 流式对话 | POST | `/api/chat_stream` | SSE，支持 tool_call/content/done 事件 |
| 清空会话 | POST | `/api/chat/clear` | 按 session_id 清理 |
| AIOps 诊断 | POST | `/api/aiops` | SSE，返回 plan/step_complete/report/complete 事件 |
| 文件上传 | POST | `/api/upload` | 上传 txt/md + 自动向量化入库 |
| 告警上报 | POST | `/api/alerts/ingest` | Bearer Token 认证 |
| 活跃告警 | GET | `/api/alerts/active` | 内存中 status=active 的告警 |
| 解决告警 | POST | `/api/alerts/{id}/resolve` | 标记 resolved |
| 心跳上报 | POST | `/api/heartbeat` | Agent 心跳 + 系统快照（含 CPU/内存/进程）|

## 快速开始

### 环境要求

- Python 3.11+（<3.14）
- Docker（用于 Milvus 向量数据库）
- 可访问的 OpenAI 兼容 LLM API（默认 DeepSeek）

### 安装与启动

```bash
# 1. 安装依赖
pip install uv
uv sync

# 2. 配置 .env（LLM API Key 等，参考 .env 文件内注释）

# 3. 启动（Linux/macOS）
make init    # Docker + MCP + FastAPI + 文档上传

# 或 Windows
.\start-windows.bat
```

### 访问

| 服务 | 地址 |
|------|------|
| Web UI | http://localhost:9900 |
| API 文档 (Swagger) | http://localhost:9900/docs |
| Monitor MCP | http://localhost:8004/mcp |
| Milvus Attu (管理 UI) | http://localhost:8000 |
| MinIO Console | http://localhost:9001 (minioadmin/minioadmin) |

### 调用示例

```bash
# 健康检查
curl http://localhost:9900/health

# 快速对话
curl -X POST "http://localhost:9900/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"Id":"session-123","Question":"最近有什么告警？"}'

# AIOps 流式诊断
curl -X POST "http://localhost:9900/api/aiops" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"session-123"}' --no-buffer
```

## 配置说明

关键环境变量（`.env`）：

```bash
# LLM（OpenAI 兼容，可切换 DeepSeek/DashScope/OpenAI）
DASHSCOPE_API_KEY=sk-xxx
DASHSCOPE_API_BASE=https://api.deepseek.com/v1
DASHSCOPE_MODEL=deepseek-chat
DASHSCOPE_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5

# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530

# RAG
RAG_TOP_K=3
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100

# MCP（本机 Monitor）
MCP_MONITOR_URL=http://localhost:8004/mcp

# SMTP 告警邮件
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_USER=xxx@163.com
SMTP_PASS=xxx
SMTP_TO=xxx@163.com

# 告警 Webhook Token
ALERT_WEBHOOK_TOKEN=your_token_here

# OnCall 温度告警
ONCALL_TEMP_THRESHOLD_C=50
ONCALL_TEMP_DURATION_SEC=60
ONCALL_TEMP_COOLDOWN_SEC=120
ONCALL_HEARTBEAT_TIMEOUT_SEC=60
ONCALL_AUTO_DIAGNOSIS=true
EMERGENCY_SNAPSHOT_TEMP_C=85

# 进程监控（检测 MATLAB 等长时间运行进程的崩溃）
ONCALL_MONITOR_PROCESS=MATLAB.exe
ONCALL_MONITOR_CRASH_DIR=%APPDATA%\MathWorks\MATLAB\CrashDumps
ONCALL_MONITOR_CRASH_PATTERN=matlab_crash_dump.*
```

## 分层记忆系统

```
memory/
├── MEMORY.md        # Hot 层：最近 20 条诊断索引表格（摘要 + 报告链接）
│                    #   由 Planner/RAG Agent 每轮加载注入 Prompt
├── incidents/       # Cold 层：完整诊断报告 (*.md)
│                    #   每次 AIOps 完成后由 MemoryWriter 自动写入
│                    #   同时自动索引到 Milvus 供后续召回
├── artifacts/       # 归档层：Microcompact 压缩的工具原始结果
│                    #   按 session_id 分目录存储
└── emergency/       # 紧急快照：温度 ≥ 85°C 时 lhm_alert_agent 写入
                     #   last_breath.json 含 CPU/内存/进程快照
```

## Skill 系统

在 `.claude/skills/` 目录下创建 Markdown 文件，格式：

```markdown
---
id: cpu_high_diagnosis
name: CPU 高使用率诊断流程
description: 当 CPU 使用率超过 80% 时的标准诊断步骤
---

## 排查步骤

1. 使用 query_cpu_metrics 获取 CPU 使用率趋势
2. 使用 search_log 检查错误日志
3. 使用 query_memory_metrics 排除内存压力
...
```

Planner 会在制定诊断计划时自动加载所有 Skill，优先匹配标准流程。

## MCP 工具

### Monitor MCP Server (monitor_server.py)

| 工具 | 功能 |
|------|------|
| `query_cpu_metrics` | 查询本机 CPU 使用率（psutil 实时数据） |
| `query_memory_metrics` | 查询本机内存使用率 |
| `list_lhm_sensors` | 列出 LibreHardwareMonitor 所有温度传感器 |
| `get_lhm_temperature` | 获取匹配传感器的当前温度 |

## 本地工具

| 工具 | 功能 |
|------|------|
| `retrieve_knowledge` | 从 Milvus 向量库检索相关知识文档 |
| `get_current_time` | 获取当前时间（支持指定时区） |
| `search_log` | 检索本地 `logs/app_*.log` 运行日志 |

## 开发

```bash
make help       # 查看所有命令
make dev        # 开发模式（热重载）
make test       # 运行测试 + 覆盖率
make format     # 代码格式化 (ruff)
make lint       # 代码检查
make eval       # AIOps Eval 评分 (RAG检索 + LLM输出)
```

## Agent 评估体系 (Eval Harness)

基于 8 个合成崩溃场景的自动化质量评测，覆盖 RAG 检索与 LLM 生成两端：

```bash
make eval         # 全量 8 场景
make eval-one SID=01  # 单场景
```

### 检索评测（纯向量计算，不调 LLM）

| 指标 | 含义 |
|------|------|
| **Recall@K** | 标注文档中被检索到的比例 — 测"覆盖面" |
| **Precision@K** | 返回文档中相关文档的比例 — 测"噪声率" |
| **MRR** | 第一个相关文档的排名倒数 — 测"排序质量" |

### 生成评测（LLM-as-Judge）

结构化评分量规（Structured Output, temperature=0）：

| 子维度 | 分值 | 评估标准 |
|--------|:--:|------|
| 根因准确性 | 0-4 | 是否准确识别了真实根因 |
| 证据引用 | 0-3 | 是否引用了具体数据/调用栈/日志 |
| 建议可操作性 | 0-3 | 处理建议是否具体可执行 |

总分 0-10，每次跑完自动存 `.last_eval_score`，下次跑显示对比值。

场景定义在 `tests/eval_scenarios/scenario_XX.json`，每个场景含虚拟告警 payload + `expected` 期望关键词 + `relevant_docs` 应检索文档。

## 许可证

MIT License

author: chief
