# SuperBizAgent 项目分析报告

> 版本：v1.2.1 | 分析日期：2026-05-07 | 作者：chief

---

## 一、项目整体业务含义

**SuperBizAgent** 是一个**企业级智能 OnCall 运维助手系统**，核心目标是降低 OnCall 工程师的排查负担，通过 AI Agent 自动完成故障诊断和知识问答。

### 业务场景

- **场景 A — 知识问答**：运维人员遇到问题，直接向 Agent 提问，Agent 从知识库（向量数据库）检索相关文档，结合 LLM 生成准确回答
- **场景 B — 自动诊断**：系统出现告警时，Agent 自动调用 MCP 工具（日志查询 + 监控数据），按 Plan-Execute-Replan 流程逐步排查，最终输出根因分析报告

### 技术选型动机

| 选择 | 理由 |
|------|------|
| 阿里云 DashScope (Qwen-Max) | 国内可访问、OpenAI 兼容模式、中文能力强 |
| Milvus 向量数据库 | 高性能、支持海量向量检索、本地 Docker 部署 |
| LangGraph Plan-Execute | 官方推荐的 Agent 多步推理模式 |
| MCP 协议 | 标准化的工具接入协议，Agent 可动态发现和调用外部工具 |
| 纯静态前端 | 零构建、零依赖（仅 CDN 引入 marked + highlight.js） |

---

## 二、核心功能模块拆解

### 模块 1：RAG 智能对话（知识库问答）

```
用户提问 → 向量检索(Top-K) → 拼接上下文 → LLM生成回答
```

**涉及文件：**

| 文件 | 职责 |
|------|------|
| [app/services/rag_agent_service.py](app/services/rag_agent_service.py) | 核心 RAG Agent，基于 LangGraph + ChatQwen |
| [app/tools/knowledge_tool.py](app/tools/knowledge_tool.py) | 知识检索工具（`retrieve_knowledge`） |
| [app/services/vector_store_manager.py](app/services/vector_store_manager.py) | Milvus VectorStore 封装 |
| [app/services/vector_search_service.py](app/services/vector_search_service.py) | 底层向量搜索 |
| [app/services/vector_embedding_service.py](app/services/vector_embedding_service.py) | DashScope Text Embedding v4（1024维） |
| [app/services/vector_index_service.py](app/services/vector_index_service.py) | 文档索引编排 |
| [app/services/document_splitter_service.py](app/services/document_splitter_service.py) | 文档分块（Markdown 标题分割 + 递归字符分割 + 小片段合并） |
| [app/tools/time_tool.py](app/tools/time_tool.py) | 当前时间工具 |

**数据流：**

1. 用户上传 `.md`/`.txt` → 按标题+字符分块（chunk_size=800, overlap=100）
2. DashScope Embedding API 向量化 → 存入 Milvus `biz` collection
3. 用户提问 → `retrieve_knowledge` 工具检索 Top-K 文档
4. 拼接 system prompt + 检索结果 → Qwen-Max 生成回答

### 模块 2：AIOps 智能运维（Plan-Execute-Replan）

```
Planner(制定计划) → Executor(执行步骤+调用MCP工具) → Replanner(评估/调整/生成报告)
       ↑                                                            |
       └──────────────── 循环直至信息充足 ───────────────────────────┘
```

**涉及文件：**

| 文件 | 职责 |
|------|------|
| [app/services/aiops_service.py](app/services/aiops_service.py) | Plan-Execute-Replan 工作流编排 |
| [app/agent/aiops/planner.py](app/agent/aiops/planner.py) | 计划制定器（先检索知识库获取经验，再参考可用工具制定步骤） |
| [app/agent/aiops/executor.py](app/agent/aiops/executor.py) | 步骤执行器（LLM 绑定工具 → 决策调用 → ToolNode 执行 → LLM 总结） |
| [app/agent/aiops/replanner.py](app/agent/aiops/replanner.py) | 重规划器（三种决策：respond / continue / replan） |
| [app/agent/aiops/state.py](app/agent/aiops/state.py) | LangGraph 状态定义 |
| [app/agent/aiops/utils.py](app/agent/aiops/utils.py) | 工具描述格式化 |

**Replanner 决策逻辑：**

- **优先 respond** — 信息足够就结束，不追求完美
- **其次 continue** — 当前计划合理，继续执行
- **最后 replan** — 严格限制：已执行≥5步禁止 replan，新步骤数不能超过剩余步骤数
- **硬限制**：最多执行 8 步，超过强制生成报告

**决策口诀**："优先结束 > 保持不变 > 调整计划"

### 模块 3：MCP 工具服务

#### CLS Server（[mcp_servers/cls_server.py](mcp_servers/cls_server.py)）— 端口 8003

| 工具 | 说明 |
|------|------|
| `get_current_timestamp` | 获取毫秒时间戳 |
| `get_region_code_by_name` | 地区码查询 |
| `get_topic_info_by_name` | 日志主题查询 |
| `search_topic_by_service_name` | 按服务名搜索日志主题（支持模糊匹配） |
| `search_log` | 日志搜索（按时间范围+查询语句） |

#### Monitor Server（[mcp_servers/monitor_server.py](mcp_servers/monitor_server.py)）— 端口 8004

| 工具 | 说明 |
|------|------|
| `query_cpu_metrics` | CPU 使用率时序数据（动态生成趋势数据） |
| `query_memory_metrics` | 内存使用率时序数据 |

> 当前为 Mock 数据，可替换为真实腾讯云 CLS SDK / Prometheus 等

### 模块 4：文件上传与知识库管理

**涉及文件：** [app/api/file.py](app/api/file.py) + [app/services/vector_index_service.py](app/services/vector_index_service.py)

- 上传 `.txt`/`.md`，最大 10MB
- 自动分块 → 向量化 → 存入 Milvus
- 支持覆盖更新（同文件重新上传会先删除旧数据）
- 已内建 `aiops-docs/` 目录下的 5 个运维知识文档：

| 文档 | 大小 | 内容 |
|------|------|------|
| `cpu_high_usage.md` | 3.6 KB | CPU 使用率过高排查方案 |
| `disk_high_usage.md` | 7.7 KB | 磁盘使用率过高排查方案 |
| `memory_high_usage.md` | 5.4 KB | 内存使用率过高排查方案 |
| `service_unavailable.md` | 7.5 KB | 服务不可用排查方案 |
| `slow_response.md` | 6.6 KB | 服务响应慢排查方案 |

### 模块 5：会话管理

- 基于 LangGraph MemorySaver（内存检查点）
- 支持会话持久化（thread_id 机制）
- 消息历史修剪（保留最近 6 条，超过 7 条自动裁剪）
- 前端 localStorage 存储历史对话列表（最多 50 条）

---

## 三、系统架构设计

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Frontend (Static)                           │
│                    index.html + app.js + styles.css                 │
│                  SSE EventSource / Fetch API / FormData             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP (port 9900)
┌──────────────────────────────▼──────────────────────────────────────┐
│                      FastAPI Application                            │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────┐   │
│  │ /api/chat │  │ /api/     │  │ /api/     │  │ /api/health   │   │
│  │ /api/     │  │ aiops     │  │ upload    │  │               │   │
│  │ chat_     │  │ (SSE)     │  │           │  │               │   │
│  │ stream    │  │           │  │           │  │               │   │
│  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └───────┬───────┘   │
│        │              │              │                │             │
│  ┌─────▼──────┐ ┌─────▼──────────┐ ┌─▼────────────┐  │             │
│  │ RAG Agent  │ │ AIOps Service  │ │ Vector Index │  │             │
│  │ Service    │ │ (Plan-Exec-    │ │ Service      │  │             │
│  │ (LangGraph │ │  Replan)       │ │              │  │             │
│  │  + ChatQwen│ │                │ │              │  │             │
│  └──┬───┬─────┘ └───┬────┬───────┘ └──┬───┬───────┘  │             │
│     │   │           │    │             │   │          │             │
│     │   │    ┌──────┘    │             │   │          │             │
│     │   │    │     ┌─────┘             │   │          │             │
│     │   │    │     │                   │   │          │             │
│  ┌──▼───▼────▼─────▼──────┐    ┌───────▼───▼──────┐   │             │
│  │   MCP Client           │    │  Milvus Manager  │   │             │
│  │   (MultiServerMCP)     │    │  (pymilvus)      │   │             │
│  │   + Retry Interceptor  │    │  collection: biz │   │             │
│  └──────────┬─────────────┘    └───────┬──────────┘   │             │
│             │                          │               │             │
└─────────────┼──────────────────────────┼───────────────┼─────────────┘
              │                          │               │
    ┌─────────▼──────────┐    ┌──────────▼──────────┐   │
    │ CLS MCP Server     │    │ Monitor MCP Server  │   │
    │ (port 8003)        │    │ (port 8004)         │   │
    │ FastMCP + Mock     │    │ FastMCP + Mock      │   │
    └────────────────────┘    └─────────────────────┘   │
                                                  ┌─────▼──────────────┐
                                                  │ Milvus Standalone  │
                                                  │ (port 19530)       │
                                                  │ + Etcd + MinIO     │
                                                  │ + Attu Web UI      │
                                                  │   (port 8000)      │
                                                  └────────────────────┘
                                            ┌──────────────────────────┐
                                            │  Alibaba Cloud DashScope │
                                            │  Qwen-Max (LLM)         │
                                            │  text-embedding-v4 (Emb)│
                                            └──────────────────────────┘
```

### 分层架构（代码层面）

| 层 | 目录 | 职责 |
|---|------|------|
| **路由层** | `app/api/` | HTTP 请求处理、SSE 流式适配、输入验证 |
| **服务层** | `app/services/` | 业务逻辑编排（RAG Agent、AIOps 工作流、向量索引） |
| **Agent 层** | `app/agent/` | LangGraph 节点实现（Planner/Executor/Replanner）、MCP 客户端管理 |
| **工具层** | `app/tools/` | LangChain Tool 定义（知识检索、时间查询） |
| **数据模型层** | `app/models/` | Pydantic 请求/响应模型 |
| **核心层** | `app/core/` | LLM 工厂、Milvus 客户端管理（连接/建表/索引） |
| **工具层** | `app/utils/` | 日志配置（Loguru） |
| **MCP 服务** | `mcp_servers/` | 独立的 MCP Server 进程（CLS、Monitor） |
| **前端** | `static/` | 纯静态 HTML/CSS/JS |
| **知识库** | `aiops-docs/` | 运维知识文档（RAG 数据源） |

### API 接口一览

| 功能 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 普通对话 | POST | `/api/chat` | 一次性返回 |
| 流式对话 | POST | `/api/chat_stream` | SSE 流式输出 |
| AIOps 诊断 | POST | `/api/aiops` | 自动故障诊断（SSE 流式） |
| 文件上传 | POST | `/api/upload` | 上传并索引文档 |
| 索引目录 | POST | `/api/index_directory` | 批量索引指定目录 |
| 健康检查 | GET | `/api/health` | 服务状态检查 |
| 会话历史 | GET | `/api/chat/session/{id}` | 查询会话消息历史 |
| 清空会话 | POST | `/api/chat/clear` | 清空指定会话 |

### SSE 事件类型（对话流）

| 事件类型 | 说明 |
|---------|------|
| `content` | 流式内容块（实时渲染 Markdown） |
| `tool_call` | 工具调用状态通知 |
| `search_results` | RAG 检索结果 |
| `done` | 流式输出完成 |
| `error` | 错误信息 |

### SSE 事件类型（AIOps 诊断流）

| 事件类型 | 阶段 | 说明 |
|---------|------|------|
| `plan` | plan_created | 诊断计划制定完成 |
| `step_complete` | step_executed | 单个步骤执行完成 |
| `report` | final_report | 最终诊断报告生成 |
| `complete` | diagnosis_complete | 诊断流程完成 |
| `error` | - | 异常信息 |

### 关键设计决策

1. **全局单例模式** — 所有 Service、Manager 都是模块级单例（`rag_agent_service`、`aiops_service`、`milvus_manager`、`vector_store_manager` 等）
2. **延迟初始化** — Agent 中的 MCP 工具在首次查询时异步加载，避免启动时阻塞
3. **会话隔离** — 通过 LangGraph `thread_id` 机制实现多会话隔离
4. **流式输出** — 全部对话/诊断接口使用 SSE（Server-Sent Events），非 WebSocket
5. **重试机制** — MCP 工具调用内置指数退避重试（最多 3 次，1s/2s/4s）
6. **内存存储** — 会话检查点使用 MemorySaver（进程重启丢失），适合单机部署

---

## 四、按前后端拆分的开发任务

### 后端任务

#### 第一梯队：基础设施补全

| 任务 | 说明 | 优先级 |
|------|------|--------|
| 添加 Redis 会话存储 | 替换 MemorySaver，支持服务重启后会话不丢失 | P0 |
| 配置管理增强 | 支持多环境配置（dev/staging/prod），敏感信息加解密 | P0 |
| 单元测试搭建 | 测试目录 `tests/` 已配置但为空，需补充核心模块测试 | P0 |
| Docker 化主服务 | 编写 Dockerfile，将 FastAPI + MCP 服务容器化 | P1 |

#### 第二梯队：功能增强

| 任务 | 说明 | 优先级 |
|------|------|--------|
| MCP Server 接入真实 API | CLS Server 对接腾讯云 CLS SDK，Monitor 对接 Prometheus/Grafana | P0 |
| 增加更多监控指标工具 | 磁盘 I/O、网络流量、进程列表、JVM 指标等 | P1 |
| 告警源对接 | 对接 Prometheus AlertManager / 腾讯云监控告警，实现事件驱动诊断 | P1 |
| 诊断报告持久化 | 将诊断结果存入数据库，支持历史查询和趋势分析 | P1 |
| 多模型支持 | LLM Factory 已预留 OpenAI 兼容接口，支持切换到其他模型 | P2 |
| RAG 检索增强 | 混合检索（向量 + BM25）、重排序（Reranker）、Query 改写 | P2 |
| 并发控制 | 添加请求队列/限流，防止 LLM API 过载 | P2 |

#### 第三梯队：运维与质量

| 任务 | 说明 | 优先级 |
|------|------|--------|
| 可观测性 | 添加 Prometheus metrics、请求追踪（trace_id） | P1 |
| CI/CD | GitHub Actions / Jenkins pipeline | P1 |
| API 版本化 | `/api/v1/chat` 等版本化路由 | P2 |
| 数据库迁移工具 | Alembic 管理 Milvus schema 变更 | P2 |

### 前端任务

#### 第一梯队：基础体验

| 任务 | 说明 | 优先级 |
|------|------|--------|
| 错误处理优化 | 统一错误码映射、网络断开重连提示、超时处理 | P0 |
| 流式对话体验优化 | 流式输出中断/恢复、Token 级渲染（当前为全量 Markdown 刷新） | P0 |
| AIOps 诊断进度可视化 | 实时显示当前执行步骤、工具调用状态（类似 CI/CD Pipeline） | P0 |
| 移动端适配 | 响应式布局，支持手机浏览器 | P1 |

#### 第二梯队：功能增强

| 任务 | 说明 | 优先级 |
|------|------|--------|
| Markdown 渲染优化 | 支持表格、Mermaid 图表、任务列表等扩展语法 | P1 |
| 会话管理增强 | 会话重命名、批量删除、搜索历史对话 | P1 |
| 知识库管理界面 | 查看已索引文档列表、手动删除/重新索引 | P1 |
| 暗色模式 | 主题切换 | P2 |
| 多语言 | 中英文切换 | P2 |

#### 第三梯队：架构演进

| 任务 | 说明 | 优先级 |
|------|------|--------|
| 前端框架迁移 | 考虑迁移到 React/Vue（当前纯 JS 1550+ 行，可维护性下降） | P2 |
| TypeScript 化 | 类型安全 | P2 |
| WebSocket 替代 SSE | 支持双向通信、更精确的状态同步 | P2 |

---

## 五、技术栈一览

| 类别 | 技术 | 版本 |
|------|------|------|
| **语言** | Python | >=3.11, <3.14 |
| **Web 框架** | FastAPI | >=0.109.0 |
| **LLM 框架** | LangChain + LangGraph | >=0.1.0 / >=0.0.40 |
| **LLM 适配** | langchain-qwq (ChatQwen) | >=0.3.4 |
| **LLM 提供商** | 阿里云 DashScope (Qwen-Max) | - |
| **向量数据库** | Milvus (pymilvus + langchain-milvus) | v2.5.10 |
| **MCP 框架** | FastMCP + langchain-mcp-adapters | >=2.14.0 / >=0.2.1 |
| **流式输出** | sse-starlette | >=2.1.0 |
| **日志** | Loguru | >=0.7.2 |
| **数据校验** | Pydantic + pydantic-settings | >=2.5.0 |
| **包管理** | uv | - |
| **前端** | 原生 HTML/CSS/JS + marked.js + highlight.js | - |
| **容器** | Docker Compose (Milvus + Etcd + MinIO + Attu) | - |

---

## 六、部署架构

```
┌─────────────────────────────────────────────────────────────┐
│                       宿主机 (localhost)                     │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐   │
│  │ FastAPI      │  │ CLS MCP     │  │ Monitor MCP      │   │
│  │ :9900        │  │ Server :8003│  │ Server :8004     │   │
│  └─────────────┘  └─────────────┘  └──────────────────┘   │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Docker Compose (milvus network)                      │  │
│  │                                                      │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ etcd     │  │ MinIO    │  │ milvus-standalone│  │  │
│  │  │ :2379    │  │ :9000/01 │  │ :19530           │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  │                                                      │  │
│  │  ┌──────────┐                                        │  │
│  │  │ Attu     │  (Milvus Web UI)                       │  │
│  │  │ :8000    │                                        │  │
│  │  └──────────┘                                        │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  外部依赖: Alibaba Cloud DashScope API                       │
└─────────────────────────────────────────────────────────────┘
```

### 服务端口汇总

| 服务 | 端口 | 说明 |
|------|------|------|
| FastAPI 主服务 | 9900 | API 接口 + Web 前端 |
| CLS MCP Server | 8003 | 日志查询工具服务 |
| Monitor MCP Server | 8004 | 监控数据工具服务 |
| Milvus | 19530 | 向量数据库 |
| Milvus Health | 9091 | Milvus 健康检查 |
| Attu | 8000 | Milvus Web 管理界面 |
| MinIO | 9000 | S3 兼容对象存储（Milvus 数据存储） |
| MinIO Console | 9001 | MinIO Web 管理界面 |

---

## 七、启动流程

```
make init
  ├── 步骤1: docker compose up -d (Milvus + Etcd + MinIO + Attu)
  ├── 步骤2: 启动 CLS MCP Server (port 8003)
  ├── 步骤3: 启动 Monitor MCP Server (port 8004)
  ├── 步骤4: 启动 FastAPI (port 9900)
  ├── 步骤5: 等待服务就绪 (健康检查)
  └── 步骤6: 上传 aiops-docs/*.md 到向量库
```

Windows 用户使用 `start-windows.bat` 一键启动。

---

## 八、项目文件统计

| 目录 | 文件数 | 核心职责 |
|------|--------|---------|
| `app/api/` | 4 | HTTP 路由处理 |
| `app/services/` | 6 | 业务逻辑服务 |
| `app/agent/` | 5 | AI Agent 核心逻辑 |
| `app/agent/aiops/` | 4 | Plan-Execute-Replan 节点 |
| `app/tools/` | 2 | LangChain 工具定义 |
| `app/models/` | 4 | Pydantic 数据模型 |
| `app/core/` | 2 | LLM 工厂 + Milvus 客户端 |
| `app/utils/` | 1 | 日志配置 |
| `mcp_servers/` | 2 | MCP 工具服务器 |
| `static/` | 3 | Web 前端 |
| `aiops-docs/` | 5 | 运维知识库文档 |
| **总计** | **~42** | |

---

*本文档基于项目 v1.2.1 全部源代码分析生成，涵盖 42 个文件、约 5000+ 行代码。*
