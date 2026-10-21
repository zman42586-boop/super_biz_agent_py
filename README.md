# SuperBizAgent

面向 MATLAB 桌面进程的监控与 AIOps 诊断项目。CPU、内存由 windows_exporter 提供，本地 Prometheus 持续抓取保存，Agent 通过 MCP 查询真实当前值和历史趋势。独立事件代理检测 MATLAB 退出、上传关键日志和 Dump 摘要，结合 Milvus 知识库诊断。温度采集及 LHM 依赖已移除。

项目只保留面向告警诊断的 Plan-Execute-Replan 主链路；旧的普通多轮 RAG 聊天接口已经移除。知识库检索仍作为 AIOps Agent 的按需工具使用。

> 当前定位是个人/实验环境的工程原型，已接入 Prometheus，但尚未完成用户真实云服务器部署。旧评测来自 40 条可回答查询和 10 条不可回答查询，不能等同于线上真实故障准确率。

## 当前能力

- 指标监控：windows_exporter + 本地 Prometheus，默认每5秒抓取，保留15天；MCP按主机和时间查询，可选 remote_write 同步云端副本。
- MATLAB 退出检测：只有观察到进程从存活变为消失，才触发“进程异常退出”流程；如果能找到 dump 就附带 dump，否则仍会上报退出事件。
- 主机事件：默认每5秒检测进程；保留退出、Dump解析与增量日志上传，不再自行采样CPU/内存、Top进程或温度。
- 心跳与历史证据：独立线程默认每5秒发主机存活信息；MySQL保存主机状态、关键日志及任务，连续指标历史保存在Prometheus；旧版指标心跳仍兼容。
- 持久化诊断：进程退出创建 `alert_diagnosis`；持续失联创建 `host_down_diagnosis`，由 Harness Worker 执行已有 LangGraph。
- Worker 恢复：运行期间周期回收租约；每次认领递增现有 `version` 作为 Fencing Token，续租及普通写入不递增；关键写入校验所有权，旧 Worker 无法覆盖接管后的状态。
- 失联降级：禁用主机实时工具和`search_log`，允许读取独立Prometheus中截止最后心跳的历史；数据源不可用时退回冻结日志/旧快照与RAG。
- RAG 诊断：Dense 向量召回 + Milvus BM25 稀疏召回，经 RRF 融合后按置信度决定是否查询改写、重试和交叉编码重排。
- 证据验证：原始事实和知识先登记 Evidence ID，再经确定性引用校验和独立 LLM 语义验证；只渲染验证后的结构化结论，失败时有限修正或报告“原因未确定”。
- 分层记忆：诊断报告先写入 `memory/incidents/` 留档；LLM Judge 评估知识价值，高价值低风险案例自动入库，模糊或高风险案例转人工审核，只有 `approved` 内容能进入 Milvus 和 Planner 上下文。
- 评测：支持 Recall@5、Precision@5、MRR、nDCG@5、Hit@1、拒答率、重试率、重排率和延迟统计；生成质量由独立 LLM Judge 评分。

## 真实链路

```text
每 5 秒轮询 MATLAB.exe
        |
        +-- 独立指标链路：windows_exporter -> 本地 Prometheus -> 可选云端副本
        +-- 独立心跳：host -> MySQL 主机存活状态；MATLAB关键日志单独上传
        |
        `-- 上轮存活、本轮消失
                |
                +-- 扫描最新 crash dump，解析异常类型/调用栈/错误行
                `-- POST /api/alerts/ingest
                        |
                        +-- 告警去重与邮件
                        `-- MySQL pending Run -> Harness Worker
                                `-- LangGraph Planner -> Executor -> Microcompact -> Replanner -> Evidence Verifier -> Final Report
                                |
                                +-- Milvus 混合检索与证据置信度
                                +-- Prometheus MCP 当前/历史 CPU、内存指标；冻结日志工具
                                `-- 事实 + 证据 + 推断的诊断报告
                                        |
                                        +-- 写入分层记忆
                                        `-- 离线时由独立 Judge 模型评分
```

注意：DeepSeek Flash 负责候选诊断及独立 Prompt 的在线 Evidence Verifier；DeepSeek Pro 只在评测脚本中作为 Judge 使用。在线验证复用同一模型，仍存在相关性偏差，需要人工抽检。使用不同模型能降低“同一模型生成后再给自己打分”的相关性偏差，但不能消除 Judge 偏差，所以仍需人工标注集和规则指标。

## RAG 实现

主机失联、租约回收、Fencing Token、数据库迁移和配置见 [持久化 OnCall 说明](docs/durable-oncall.md)。已有数据库需先停 API 和全部旧 Worker，运行 `scripts/migrate_harness_v2.py` 创建新表；保留现有 `version` 数值，可能已部署的 `lease_epoch` 列保留但不再使用。诊断完成与通知原子入队，SMTP 不确定交付不会自动重发，不能承诺邮件 exactly-once。

### 文档与切分

仓库目前包含 36 篇 MATLAB/AIOps 文档和 50 篇通用 OnCall 文档。`aiops-docs/` 中新增了 MathWorks 官方排障摘要以及公开案例的工程化整理；文件是 Markdown，但不是把网页原文整页复制进仓库。新索引 metadata 包含基于来源与内容版本的 document/parent/chunk ID；已有 Milvus 不会自动重建，离线 live_pool 可只读映射旧 ID。

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
| 输出 | Parent 章节级去重 | Top 5 |

BM25 属于召回阶段，不是重排器。它依靠词频、逆文档频率和文档长度归一化，擅长匹配错误码、函数名和日志关键词。Dense 召回把查询和文档分别编码成向量，IP 在归一化 BGE 向量上等价于余弦相似度。CrossEncoder 则把“查询 + 候选文档”一起送进模型逐对打分，通常更准但 CPU 延迟明显更高。

这里使用 FLAT 而不是 IVF_FLAT/NLIST，因为当前知识库只有几百个 chunk，精确遍历简单且不会引入近似召回损失。语料扩大到数十万或百万向量后，再比较 HNSW、IVF_FLAT 或 IVF_PQ 的速度、内存和召回率。

### 自适应检索

1. 对原查询执行 Dense + BM25 + RRF。
2. 应用层保留每个 Child 的 Dense 排名与 BM25 排名；同一 Child 同时进入两路 Top 10 才标记为高置信度。
3. 高置信度唯一 Parent 数量达到最终 Top K 时，直接按 RRF 顺序返回并跳过 CrossEncoder。
4. 高置信度 Parent 不足时，使用 CrossEncoder 对 RRF 候选池重排，并以最高语义分数校验证据是否充分；没有候选时只做一次规则化查询改写。
5. 输出检索尝试次数、是否改写、是否重排、证据是否充分，供日志和评测使用。

当前双路排名窗口为 Top 10；该参数来自当前工程设定，仍需结合中文故障检索集校准。

## 模型与基础设施

| 用途 | 默认配置 |
|---|---|
| 诊断/问答 LLM | `deepseek-v4-flash`（OpenAI-compatible API） |
| Eval Judge | `deepseek-v4-pro` |
| Embedding | `BAAI/bge-small-zh-v1.5`，本地运行 |
| Reranker | `BAAI/bge-reranker-base`，本地运行 |
| 向量数据库 | Milvus 2.5 + etcd + MinIO，Docker Compose |
| Agent 编排 | LangGraph Plan-Execute-Replanner |
| 指标监控 | windows_exporter 0.31.9 + Prometheus 3.15.0 + 只读 MCP |

Milvus 用于同时保存 Dense、BM25 sparse、正文和 JSON metadata，并在同一数据库中执行混合检索。对于更小的 demo，也可以选 FAISS/Chroma；偏关系型业务可选 PostgreSQL + pgvector；托管场景可选 Pinecone、Weaviate、Qdrant 或云厂商向量检索。本项目保留 Milvus，是因为它直接支持当前的 Dense + Sparse + RRF 结构，也便于以后扩容。

## 快速开始（Windows）

要求：Python 3.11–3.13、MySQL 8（SKIP LOCKED）、Docker Desktop、可用的 OpenAI-compatible API key。Docker Desktop 需要先由用户启动；仓库脚本不会可靠地替你启动桌面程序。

```powershell
git clone https://github.com/zman42586-boop/super_biz_agent_py.git
cd super_biz_agent_py
Copy-Item .env.example .env
# 编辑 .env，至少填写 DASHSCOPE_API_KEY

pip install uv
uv sync

# 下载并校验官方便携监控程序，仅首次安装需要
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-monitoring.ps1

docker compose -f vector-database.yml up -d
docker compose -f vector-database.yml ps
```

首次运行会下载 BGE embedding 和 reranker；其中 reranker 约 1.1 GB，CPU 首次加载和推理会比较慢。

启动服务：

```powershell
.\start-windows.bat
```

启动脚本默认创建 3 个独立 Harness Worker 进程，以便并行认领不同的 Run。如需调整，
可在启动脚本前设置 `HARNESS_WORKER_COUNT`；单个 Worker 同一时刻仍只执行一个 Run。

单独启动事件代理（沿用旧文件名，不再依赖 LHM）：

```powershell
.\.venv\Scripts\python.exe scripts\lhm_alert_agent.py
```

常用地址：Web/API `http://localhost:9900`，Swagger `http://localhost:9900/docs`，Prometheus `http://127.0.0.1:9090`，Windows Exporter `http://127.0.0.1:9182/metrics`，Attu `http://localhost:8000`，Milvus `localhost:19530`。配置、云端同步与验收见 [Prometheus 接入](docs/prometheus-monitoring.md)。

### 长期记忆审核

诊断报告始终保存为 Incident，但不再默认进入知识库。LLM Judge 会输出知识价值分、模糊标志、风险等级和理由：分数不低于 `MEMORY_AUTO_APPROVE_SCORE` 且低风险、无歧义时自动批准；低于 `MEMORY_REJECT_SCORE` 时拒绝；其余情况进入人工审核。Judge 调用失败时也会安全降级到人工审核。

- `GET /api/memory/reviews?status=pending_review`：查看待审核 Incident。
- `POST /api/memory/reviews/{incident_name}`：提交 `approved` 或 `rejected` 的人工决定、审核人和备注。

人工批准后系统才会将 Incident 增量写入 Milvus；未批准条目不会被 `MEMORY.md` 加载进 Planner 的 Prompt。

## 关键配置

完整模板见 `.env.example`。当前核心 RAG 参数也可通过环境变量覆盖：

```dotenv
RAG_TOP_K=5
RAG_DENSE_CANDIDATES=20
RAG_SPARSE_CANDIDATES=20
RAG_RERANK_CANDIDATES=20
RAG_RRF_K=60
RAG_RERANKER_WEIGHT=0.20
RAG_CONFIDENCE_RANK_WINDOW=10
RAG_SEMANTIC_EVIDENCE_THRESHOLD=0.55
CHUNK_MAX_TOKENS=420
CHUNK_OVERLAP_TOKENS=64
```

MATLAB 监控示例：

```dotenv
POLL_INTERVAL_SEC=5
HEARTBEAT_INTERVAL_SEC=5
ONCALL_HEARTBEAT_CHECK_INTERVAL_SEC=10
ONCALL_HEARTBEAT_SUSPECTED_SEC=30
ONCALL_HEARTBEAT_TIMEOUT_SEC=60
HARNESS_LEASE_SEC=30
HARNESS_RECOVERY_INTERVAL_SEC=5
MATLAB_LOG_GLOBS=C:\matlab-logs\runtime.log*
ONCALL_HISTORY_TTL_SEC=7200
ONCALL_MONITOR_PROCESS=MATLAB.exe
ONCALL_MONITOR_CRASH_DIR=C:\Users\YOUR_NAME\AppData\Roaming\MathWorks\MATLAB\CrashDumps
ONCALL_MONITOR_CRASH_PATTERN=matlab_crash_dump.*
LOCAL_PROMETHEUS_URL=http://127.0.0.1:9090
PROMETHEUS_URL=http://127.0.0.1:9090
```

## 评测与验收（2026-10-20）

检索评测与诊断正确性分别验收。40 条查询和 10 条域外查询来自旧合成开发集，原 `relevant_docs` 没有可追溯人工复核记录，不是全库相关性真值。本轮修复了文件/Parent 混用导致 nDCG 超过 1、Precision 分母不一致、隐藏场景描述进入查询和技术失败被记成正确拒答的问题。旧 README 的 85% Recall 等记录不再作为当前效果结论。

新流程：固定语料快照 → Dense/BM25/精确词/标题元数据 Pooling → 人工 0/1/2 标注 → 固定数据集版本 → 同快照评测。未标注结果保持 unknown；主指标返回待补标，Recall 明确为已标注候选池的近似值。详见 [检索评测说明](docs/retrieval-evaluation.md)。

```powershell
.\.venv\Scripts\python.exe -m pytest --no-cov
.\scripts\test_mysql_disposable.ps1
.\.venv\Scripts\python.exe -m app.evaluation.live_pool --queries tests/retrieval_queries_v3.json --output-dir work/live-pool-v1
```

已有评测入口继续保留，但输出明确标记 legacy 文件级标签和 MRR@5。`eval_runner` 的合成场景模式禁用现场工具、Planner 记忆及 Incident 写入，并记录输入/标签/语料/配置指纹；线上模型不可用或指标缺失时不能通过回归门禁。

- [诊断验证与状态流转](docs/evidence-verification.md)
- [源码核查、改动清单与测试结果](docs/aiops-improvement-20261020.md)
- [MCP 与故障证据数据链路](docs/mcp-evidence-audit.md)
- [安全 MATLAB 样本与执行边界](tests/fault_injection/README.md)

正式验收结果、真实依赖测试和未完成事项均集中记录在核查报告中。MATLAB 样本默认仅预览，不会向现有仿真任务注入故障；本轮不实现自动修复。

## 目录说明

```text
app/                      FastAPI、LangGraph、RAG、告警和工具代码
aiops-docs/               MATLAB 官方摘要与公开案例知识文档
oncall_knowledge_docs_50/ 通用 OnCall 文档
scripts/lhm_alert_agent.py 主机事件代理（沿用旧文件名）
mcp_servers/              Prometheus 只读 MCP 工具
monitoring/               固定版本、云端副本配置；runtime本机数据不入Git
memory/                   历史诊断、索引摘要、工具归档与紧急快照
tests/                    单元测试、检索评测、生成评测和场景数据
vector-database.yml       Milvus/etcd/MinIO/Attu
```

## 已知限制

- 监控基于主动轮询，最坏检测延迟接近一个轮询周期；Agent 自身停止时依赖服务端心跳超时发现。
- “进程消失”不等于一定崩溃，用户正常退出也可能触发；是否为 crash 仍需 dump/事件日志佐证。
- CPU/内存是系统级Prometheus指标，不是MATLAB内部profiler；5秒抓取可能遗漏短尖峰。
- 本地关闭后需要云端副本才能继续查询指标；remote_write最后几秒数据可能尚未送达。
- 在线 Verifier 与生成器复用同一模型，语义判断仍有偏差；SUPPORTED 只代表当前候选通过验证，root_cause=null 时仍然没有确认根因。
- 本地 CrossEncoder 在 CPU 上延迟较高；生产化可考虑 ONNX/量化、GPU 或更小的 reranker。
- 知识库中的案例是公开资料整理与合成 runbook，不能替代组织自己的真实事故数据。

## 安全

`.env`、模型缓存、Milvus volume、日志和评测结果均被 Git 忽略。不要提交 API key、SMTP 授权码、真实客户日志或 crash dump。曾在聊天、终端或提交历史中暴露过的 key 应立即在供应商控制台轮换。

## License

MIT
