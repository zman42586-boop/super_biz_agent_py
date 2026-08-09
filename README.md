# SuperBizAgent

面向 MATLAB 桌面进程的主动轮询监控与 AIOps 诊断项目。系统持续采集 CPU、内存、温度和高占用进程；当 MATLAB 从“存在”变为“消失”时，查找最新 crash dump，把日志证据与 Milvus 知识库召回结果交给 Agent 分析，并可用独立 Judge 模型离线评估诊断报告。

> 当前定位是个人/实验环境的工程原型，不是已经接入生产监控平台的商业系统。评测数据来自 40 条可回答查询和 10 条不可回答查询，不能等同于线上真实故障准确率。

## 当前能力

- 主动轮询：默认每 5 秒检查目标进程，同时采集系统 CPU、内存和 Top 进程。
- MATLAB 退出检测：只有观察到进程从存活变为消失，才触发“进程异常退出”流程；如果能找到 dump 就附带 dump，否则仍会上报退出事件。
- 可选温度监控：通过 LibreHardwareMonitor Web API 读取温度，默认关闭，可独立开启。
- 心跳与死前快照：每轮向 FastAPI 上报状态，并在本地保存轻量快照；服务端可检测心跳超时。
- RAG 诊断：Dense 向量召回 + Milvus BM25 稀疏召回，经 RRF 融合后按置信度决定是否查询改写、重试和交叉编码重排。
- 证据约束：最终上下文分为“事实、检索证据、推断”；证据不足时明确输出“原因未确定”和需补充的日志。
- 分层记忆：诊断报告写入 `memory/incidents/`，摘要写入 `memory/MEMORY.md`，后续可重新入库参与召回。
- 评测：支持 Recall@5、Precision@5、MRR、nDCG@5、Hit@1、拒答率、重试率、重排率和延迟统计；生成质量由独立 LLM Judge 评分。

## 真实链路

```text
每 5 秒轮询 MATLAB.exe
        |
        +-- 仍存活：CPU/内存/温度/Top 进程 -> 心跳与本地快照
        |
        `-- 上轮存活、本轮消失
                |
                +-- 扫描最新 crash dump，解析异常类型/调用栈/错误行
                `-- POST /api/alerts/ingest
                        |
                        +-- 告警去重与邮件
                        `-- LangGraph Planner -> Executor -> Replanner
                                |
                                +-- Milvus 混合检索与证据置信度
                                +-- MCP 实时指标/本地日志工具
                                `-- 事实 + 证据 + 推断的诊断报告
                                        |
                                        +-- 写入分层记忆
                                        `-- 离线时由独立 Judge 模型评分
```

注意：DeepSeek Flash 负责生成诊断；DeepSeek Pro 只在评测脚本中作为 Judge 使用，不在每次线上告警中强制执行。使用不同模型能降低“同一模型生成后再给自己打分”的相关性偏差，但不能消除 Judge 偏差，所以仍需人工标注集和规则指标。

## RAG 实现

### 文档与切分

仓库目前包含 36 篇 MATLAB/AIOps 文档和 50 篇通用 OnCall 文档。`aiops-docs/` 中新增了 MathWorks 官方排障摘要以及公开案例的工程化整理；文件是 Markdown，但不是把网页原文整页复制进仓库。

Markdown 先按 H1/H2 划分 parent，再在每个 parent 内按 BGE tokenizer 切 child：

| 参数 | 当前值 | 说明 |
|---|---:|---|
| child size | 420 tokens | 不是字符数 |
| overlap | 64 tokens | 只发生在同一 H1/H2 parent 内 |
| 标题上下文 | 文档标题 + H1/H2 路径 | 写入每个 child，减少碎片失去主题 |
| source type | official/case/incident/knowledge | 支持过滤和信任级别标注 |

### 召回与重排

| 阶段 | 实现 | 当前参数 |
|---|---|---:|
| Dense 召回 | `BAAI/bge-small-zh-v1.5`，512 维，Milvus FLAT + IP | 20 candidates |
| Sparse 召回 | Milvus 内置 BM25 中文 analyzer + 倒排索引 | 20 candidates |
| 融合 | Reciprocal Rank Fusion | RRF k=60 |
| 候选 | 融合后去重 | 20 |
| 重排 | `BAAI/bge-reranker-base` CrossEncoder | 权重 0.20 |
| 输出 | 文档级去重 | Top 5 |

BM25 属于召回阶段，不是重排器。它依靠词频、逆文档频率和文档长度归一化，擅长匹配错误码、函数名和日志关键词。Dense 召回把查询和文档分别编码成向量，IP 在归一化 BGE 向量上等价于余弦相似度。CrossEncoder 则把“查询 + 候选文档”一起送进模型逐对打分，通常更准但 CPU 延迟明显更高。

这里使用 FLAT 而不是 IVF_FLAT/NLIST，因为当前知识库只有几百个 chunk，精确遍历简单且不会引入近似召回损失。语料扩大到数十万或百万向量后，再比较 HNSW、IVF_FLAT 或 IVF_PQ 的速度、内存和召回率。

### 自适应检索

1. 对原查询执行 Dense + BM25 + RRF。
2. 根据诊断标记命中、词汇覆盖、来源集中度和来源可信度计算置信度。
3. 低置信度时只做一次规则化查询改写并重试，避免无限循环。
4. 高置信度直接返回；中等置信度使用 CrossEncoder；重试后仍低且无语义证据时拒绝给出确定根因。
5. 输出检索尝试次数、是否改写、是否重排、证据是否充分，供日志和评测使用。

当前阈值：low=0.35、high=0.55、语义证据阈值=0.55。阈值来自当前小型验证集，只应作为起点。

## 模型与基础设施

| 用途 | 默认配置 |
|---|---|
| 诊断/问答 LLM | `deepseek-v4-flash`（OpenAI-compatible API） |
| Eval Judge | `deepseek-v4-pro` |
| Embedding | `BAAI/bge-small-zh-v1.5`，本地运行 |
| Reranker | `BAAI/bge-reranker-base`，本地运行 |
| 向量数据库 | Milvus 2.5 + etcd + MinIO，Docker Compose |
| Agent 编排 | LangGraph Plan-Execute-Replanner |
| 实时工具 | MCP + psutil + LibreHardwareMonitor |

Milvus 用于同时保存 Dense、BM25 sparse、正文和 JSON metadata，并在同一数据库中执行混合检索。对于更小的 demo，也可以选 FAISS/Chroma；偏关系型业务可选 PostgreSQL + pgvector；托管场景可选 Pinecone、Weaviate、Qdrant 或云厂商向量检索。本项目保留 Milvus，是因为它直接支持当前的 Dense + Sparse + RRF 结构，也便于以后扩容。

## 快速开始（Windows）

要求：Python 3.11–3.13、Docker Desktop、可用的 OpenAI-compatible API key。Docker Desktop 需要先由用户启动；仓库脚本不会可靠地替你启动桌面程序。

```powershell
git clone https://github.com/zman42586-boop/super_biz_agent_py.git
cd super_biz_agent_py
Copy-Item .env.example .env
# 编辑 .env，至少填写 DASHSCOPE_API_KEY

pip install uv
uv sync

docker compose -f vector-database.yml up -d
docker compose -f vector-database.yml ps
```

首次运行会下载 BGE embedding 和 reranker；其中 reranker 约 1.1 GB，CPU 首次加载和推理会比较慢。

启动服务：

```powershell
.\start-windows.bat
```

单独启动监控 Agent：

```powershell
.\.venv\Scripts\python.exe scripts\lhm_alert_agent.py
```

常用地址：Web/API `http://localhost:9900`，Swagger `http://localhost:9900/docs`，Attu `http://localhost:8000`，Milvus `localhost:19530`。

## 关键配置

完整模板见 `.env.example`。当前核心 RAG 参数也可通过环境变量覆盖：

```dotenv
RAG_TOP_K=5
RAG_DENSE_CANDIDATES=20
RAG_SPARSE_CANDIDATES=20
RAG_RERANK_CANDIDATES=20
RAG_RRF_K=60
RAG_RERANKER_WEIGHT=0.20
RAG_CONFIDENCE_LOW=0.35
RAG_CONFIDENCE_HIGH=0.55
RAG_SEMANTIC_EVIDENCE_THRESHOLD=0.55
CHUNK_MAX_TOKENS=420
CHUNK_OVERLAP_TOKENS=64
```

MATLAB 监控示例：

```dotenv
POLL_INTERVAL_SEC=5
ONCALL_MONITOR_PROCESS=MATLAB.exe
ONCALL_MONITOR_CRASH_DIR=C:\Users\YOUR_NAME\AppData\Roaming\MathWorks\MATLAB\CrashDumps
ONCALL_MONITOR_CRASH_PATTERN=matlab_crash_dump.*
ONCALL_TEMP_ENABLED=false
```

## 评测与已验证结果

运行单元/集成测试：

```powershell
.\.venv\Scripts\python.exe -m pytest --no-cov
```

运行真实 Milvus 检索对照和自适应评测：

```powershell
.\.venv\Scripts\python.exe -m tests.eval_retrieval_v2
.\.venv\Scripts\python.exe -m tests.eval_adaptive_retrieval
```

### 第一轮：固定检索链对照（40 queries）

| 方案 | Recall@5 | Precision@5 | MRR | nDCG@5 | Hit@1 |
|---|---:|---:|---:|---:|---:|
| Dense 原查询 | 68.33% | 30.00% | 0.8121 | 0.6710 | 75.00% |
| Dense + 查询扩展 | 76.67% | 34.50% | 0.8600 | 0.7589 | 82.50% |
| Dense + BM25 + RRF | 82.92% | 37.00% | **0.8875** | 0.8057 | **85.00%** |
| Hybrid + CrossEncoder(0.20) | **83.75%** | **37.50%** | 0.8833 | **0.8103** | **85.00%** |

CrossEncoder 平均总延迟约 4.86 秒，而不重排的 Hybrid 约 34 毫秒。因此第二轮没有对所有查询无条件重排。

### 第二轮：自适应检索（40 answerable + 10 no-answer）

| 指标 | 结果 |
|---|---:|
| Recall@5 | 85.00% |
| Precision@5 | 38.00% |
| MRR | 0.8979 |
| nDCG@5 | 0.8204 |
| Hit@1 | 85.00% |
| 不可回答查询正确拒答率 | 100.00% |
| 可回答查询低置信度误拒率 | 2.50% |
| 查询重试率 | 46.00% |
| CrossEncoder 使用率 | 56.00% |
| 平均 / P95 延迟 | 4.30s / 6.84s |

这里的 100% 只表示这 10 条人工构造的 out-of-domain 查询全部被拒答，不代表真实环境 100% 准确。当前集合规模很小，而且开发集与留出集属于相同故障类别，后续应增加真实 dump、跨版本 MATLAB 日志、难负例，并由不同人员盲标。

### LLM-as-Judge

`tests/eval_runner.py` 先完成检索和诊断，再调用 `EVAL_JUDGE_MODEL` 独立评分：根因准确性 0–4、证据引用 0–3、建议可操作性 0–3。Judge 是生成质量评测，不应与 Recall@K 等检索指标混为一谈，也不能替代人工验收。

## 目录说明

```text
app/                      FastAPI、LangGraph、RAG、告警和工具代码
aiops-docs/               MATLAB 官方摘要与公开案例知识文档
oncall_knowledge_docs_50/ 通用 OnCall 文档
scripts/lhm_alert_agent.py 本机主动轮询 Agent
mcp_servers/              CPU/内存/温度 MCP 工具
memory/                   历史诊断、索引摘要、工具归档与紧急快照
tests/                    单元测试、检索评测、生成评测和场景数据
vector-database.yml       Milvus/etcd/MinIO/Attu
```

## 已知限制

- 监控基于主动轮询，最坏检测延迟接近一个轮询周期；Agent 自身停止时依赖服务端心跳超时发现。
- “进程消失”不等于一定崩溃，用户正常退出也可能触发；是否为 crash 仍需 dump/事件日志佐证。
- 当前 CPU/内存主要是系统级和进程快照，不是 MATLAB 内部 profiler 指标。
- 温度依赖 LibreHardwareMonitor；未启动其 Web Server 时不会获得温度。
- 本地 CrossEncoder 在 CPU 上延迟较高；生产化可考虑 ONNX/量化、GPU 或更小的 reranker。
- 知识库中的案例是公开资料整理与合成 runbook，不能替代组织自己的真实事故数据。

## 安全

`.env`、模型缓存、Milvus volume、日志和评测结果均被 Git 忽略。不要提交 API key、SMTP 授权码、真实客户日志或 crash dump。曾在聊天、终端或提交历史中暴露过的 key 应立即在供应商控制台轮换。

## License

MIT
