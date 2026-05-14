# 项目修改记录

> 日期：2026-05-07

---

## 1. 切换 LLM 到 DeepSeek

**原因**：原项目使用阿里云 DashScope (Qwen-Max)，用户改用 DeepSeek V4。

**修改文件**：

### `.env`
- `DASHSCOPE_API_KEY` → DeepSeek API Key
- `DASHSCOPE_API_BASE` → `https://api.deepseek.com/v1`
- `DASHSCOPE_MODEL` → `deepseek-chat`
- `RAG_MODEL` → `deepseek-chat`

### `app/config.py`
- 新增 `dashscope_api_base` 字段，默认值指向 DeepSeek API

### `app/core/llm_factory.py`
- 移除硬编码的 DashScope URL
- 改为读取 `config.dashscope_api_base`，支持通过 `.env` 切换任意 OpenAI 兼容提供商
- 移除 DashScope 专用的 `extra_body` 参数

---

## 2. 切换 Embedding 到本地模型

**原因**：DeepSeek 没有 Embedding API，需要替换向量化方案。

**方案**：使用本地 HuggingFace 模型 `BAAI/bge-small-zh-v1.5`（约 100MB，专为中文优化，无需 API Key）。

**修改文件**：

### `app/services/vector_embedding_service.py`
- 完全重写，移除 `DashScopeEmbeddings` 类
- 改用 `langchain_huggingface.HuggingFaceEmbeddings`
- 模型首次运行时自动从 HuggingFace Hub 下载

### `app/core/milvus_client.py`
- `VECTOR_DIM`: `1024` → `512`（匹配 `bge-small-zh-v1.5` 输出维度）
- 已有维度不匹配检测逻辑，会自动重建 collection

### `pyproject.toml`
- 移除 `dashscope>=1.14.0`
- 新增 `langchain-huggingface>=0.1.0`
- 新增 `sentence-transformers>=2.2.0`

### `.env`
- `DASHSCOPE_EMBEDDING_MODEL` → `BAAI/bge-small-zh-v1.5`

---

## 3. 安装依赖

```bash
pip install uv
python -m uv sync
```

`uv sync` 根据 `pyproject.toml` 创建 `.venv` 并安装全部依赖（163 个包）。

---

## 4. 修复启动脚本闪退问题

**根本原因**：多个问题叠加导致双击 `start-windows.bat` 闪退。

| 问题 | 原因 | 修复 |
|------|------|------|
| 脚本乱码 | 脚本以 UTF-8 保存，cmd 用 GBK 读取，中文变成乱码命令 | 所有 `echo` 改为英文 |
| FastAPI 子窗口崩溃 | `main.py` 日志含 emoji，Windows GBK 终端无法输出，loguru 抛 `UnicodeEncodeError` | 去掉 emoji，logger 加 `errors="replace"` |
| 依赖安装失败无提示 | 原脚本在 `pip install` 失败时直接 `exit /b 1` 无 `pause` | 重写脚本，跳过依赖安装（已用 `uv sync` 完成） |
| Docker 镜像未下载 | 第一次运行需下载 Milvus 镜像（约 1GB），脚本只等 10 秒 | 等待时间改为 15 秒 |

**修改文件**：

### `app/main.py`
- 移除 lifespan 日志中的所有 emoji（🚀📝🌐📚🔌✅👋）

### `app/utils/logger.py`
- stdout 输出改用 `io.TextIOWrapper` 包装，加 `errors="replace"`，避免 emoji 导致崩溃

### `start-windows.bat`
- 完全重写，所有输出改为英文
- 去掉依赖安装逻辑（前提：已执行 `uv sync`）
- 保留 Docker、MCP Server、FastAPI 启动和健康检查逻辑

---

## 5. 启动顺序

```
start-windows.bat
  ├── 检查 .venv 虚拟环境
  ├── 启动 Docker Compose (Milvus + etcd + MinIO + Attu)
  ├── 启动 CLS MCP Server    :8003
  ├── 启动 Monitor MCP Server :8004
  ├── 启动 FastAPI            :9900
  └── 上传 aiops-docs/*.md 到向量库
```

## 6. 修复对话返回 "error" 问题

**原因**：对话框中发送消息返回"抱歉，发送消息时出现错误：error"，排查发现两个问题叠加：

### 问题 A：ChatQwen 无法连接 DeepSeek

`ChatQwen` 通过 `from_env("DASHSCOPE_API_BASE")` 从 `os.environ` 读取 API 地址，但 Pydantic Settings 读取 `.env` 时不会注入系统环境变量，导致回退到 DashScope 默认地址 `dashscope-intl.aliyuncs.com`，用 DeepSeek API Key 调阿里云 API 必然失败。

**修改文件**：

| 文件 | 修改 |
|------|------|
| `app/services/rag_agent_service.py` | `ChatQwen` → `llm_factory.create_chat_model()`，移除 `langchain_qwq` 依赖 |
| `app/agent/aiops/planner.py` | 同上，移除未使用的 `config` import |
| `app/agent/aiops/executor.py` | 同上，移除未使用的 `config` import |
| `app/agent/aiops/replanner.py` | 同上 + 类型标注 `ChatQwen` → `ChatOpenAI` |

### 问题 B：MCP 工具加载失败导致整个对话崩溃

`_initialize_agent()` 在加载 MCP 工具时若连接失败（502 Bad Gateway），直接抛出异常导致对话不可用。即使 MCP 服务器未就绪，基础对话也应可用。

**修改文件**：

| 文件 | 修改 |
|------|------|
| `app/services/rag_agent_service.py` | `_initialize_agent()` 中 MCP 工具加载包裹 try-except，失败时降级为仅本地工具 |
| `app/agent/aiops/planner.py` | 同上 |
| `app/agent/aiops/executor.py` | 同上 |

### 改进：错误日志透明化

新增 `_unwrap_exception()` 辅助函数，递归解包 Python 3.11+ `ExceptionGroup`，避免"unhandled errors in a TaskGroup"这种无意义日志。

**修改文件**：`app/services/rag_agent_service.py`

---

## 7. 修复 AIOps 按钮返回 error

**原因**：点击 AIOps 按钮后，后端 `execute()` 抛异常返回 `任务执行出错: 'error'`。排查发现 `ChatOpenAI.with_structured_output()` 默认使用 `response_format`（json_mode）参数，DeepSeek API 不支持此参数，返回：
```
Error code: 400 - 'This response_format type is unavailable now'
```

`langchain_openai` 的 `with_structured_output` 在新版本中默认使用 `response_format={"type": "json_schema"}`，而 DeepSeek 只支持 function calling 模式。

**修改文件**：

| 文件 | 修改 |
|------|------|
| `app/agent/aiops/planner.py` | `with_structured_output(Plan)` → `with_structured_output(Plan, method="function_calling")` |
| `app/agent/aiops/replanner.py` | `with_structured_output(Act)` → `with_structured_output(Act, method="function_calling")` |
| `app/agent/aiops/replanner.py` | `with_structured_output(Response)` → `with_structured_output(Response, method="function_calling")` |

---

## 8. MCP 工具连接问题（已知限制）

**现象**：AIOps 运行时 MCP 工具加载失败，日志显示 `httpx.ReadError`，错误被 `anyio.TaskGroup` 包装为 `unhandled errors in a TaskGroup (1 sub-exception)`。

**根因**：`fastmcp` 2.14.5（服务器框架）和 `mcp` 1.26.0（客户端库，被 `langchain_mcp_adapters` 使用）之间 HTTP 传输层存在协议不兼容，无论是 `streamable-http` 还是 `sse` 传输都返回 `ReadError`。尝试用 `mcp.server.fastmcp.FastMCP`（内置）替换 `fastmcp` 也无效。

**当前处理**：已有优雅降级机制（第 6 节问题 B），MCP 工具不可用时 Agent 使用本地工具（`retrieve_knowledge`、`get_current_time`）继续工作。AIOps 报告会如实说明 "缺少 query_logs 工具"。

**后续修复方向**：
- 等待 `langchain_mcp_adapters` 更新兼容 `fastmcp` 2.x
- 或者用 `mcp` 原生 API 重写 MCP 客户端连接逻辑
- 或者绕过 MCP，直接在 Agent 中集成腾讯云 CLS SDK / Prometheus API

---

## 9. 服务地址

| 服务 | 地址 |
|------|------|
| Web UI | http://localhost:9900 |
| API 文档 | http://localhost:9900/docs |
| Milvus 管理界面 | http://localhost:8000 |
