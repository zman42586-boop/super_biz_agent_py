# 检索候选池、人工标注与评测

更新：2026-10-20。

## 旧数据集核查

`eval_cases_v2.json` 是 8 类各 5 条静态查询，`eval_adaptive_retrieval.py` 再增加 10 条域外查询。标签是预置文件名列表，没有标注者、理由或复核记录；无法确认是经过正式人工审定的全集。没有发现运行时检索结果反写标签的代码。

旧评测混合了文件标签和线上 Parent 章节排名。重复文件的多个 Parent 曾让 nDCG 大于 1；旧脚本按返回数量计算 Precision，v2 按 K；场景 description 还包含隐藏根因并进入检索查询。现已统一 legacy 文件级精确匹配、去重、固定 K，移除 description 泄漏；MRR 明确为截断到 K 的 MRR@K。

`eval_retrieval_v2` 和 `eval_adaptive_retrieval` 继续支持旧集合，但标记 `legacy_positive_only_closed_world`。其未列出文件视为 0 仅是旧标签的回归兼容约定，不能套到新候选池，也不能称为全库召回率。旧开发/留出划分同属相同故障类别，并非经过隔离的盲测。本轮 50 条迁移查询全部标记 development，正式留出集需独立收集。

## 两种语料快照

新索引写入稳定的 `_document_id`、`_document_version`、`_parent_id`、`_chunk_id`；版本含内容哈希，来源保留仓库相对路径，避免不同目录同名文档混淆。Milvus 的物理 UUID 不再充当人工标签的稳定主键。

已有集合不自动删除或重建。推荐直接对当前部署语料做只读快照：

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.live_pool --queries tests/retrieval_queries_v3.json --top-n 10 --output-dir work/live-pool-v1
```

该命令导出 corpus、native ID 映射、Dense/BM25 原始排名、pool 和待标 CSV。旧索引被映射为 `deployed-content-snapshot-v1` 内容版本 ID，不修改原集合。大于等于 16000 实体时拒绝截断，需另行分页导出。

也可以用生产同款预处理、H1/H2 分 Parent 和 tokenizer Child 切分构建本地文件快照：

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.cli snapshot aiops-docs oncall_knowledge_docs_50 --output work/corpus-v1.json
.\.venv\Scripts\python.exe -m app.evaluation.cli pool --corpus work/corpus-v1.json --queries tests/retrieval_queries_v3.json --dense --top-n 10 --output work/pool-v1.json --annotations work/annotations-v1.csv
```

文件快照与部署快照是不同 ID 方案，不能混用排名和标签；corpus_hash 会阻止这种比较。文件快照依赖本地 tokenizer/embedding，live_pool 依赖实际 Milvus 和 embedding，纯标注/冻结/指标模块不需要这些依赖。

## Pooling 方法

默认提供独立词法 BM25、错误码/函数名精确匹配、标题/章节/来源元数据匹配；`--dense` 用本地 BGE 计算 Dense。live_pool 额外读取 Milvus Dense 和 Milvus BM25，两路各自 Top N，在融合截断前合并，降低单一路检索的标签偏差。

支持导入独立 Dense 或 Query Expansion 检索：`--route dense_other=work/dense-run.json`，文件格式：

```json
{"corpus_hash":"快照哈希","rankings":{"query_id":[{"chunk_id":"快照中的ID","score":0.8}]}}
```

每个候选保留 document/version、unit ID、原文、来源，以及所有匹配的 route/rank/score/chunk/parent。重复候选只占一个评测单位，但不丢失召回来源。默认评测单位为 Parent，另支持 chunk/document；不得跨单位复用 qrels。

候选分数只用于构建池，不是 Ground Truth。CSV 不携带检索分数，减少标注者受系统排名影响。自动下载公开模型权重或重建集合不属于 freeze/evaluate 操作。

## 人工标注与固定版本

在 CSV 填 `grade`、`reviewer`、`reason`：

| grade | 定义 |
|---|---|
| 2 | 能直接帮助诊断，支持关键排查或根因判断 |
| 1 | 部分相关，提供排查方向 |
| 0 | 不相关 |
| 空白 | 未标注；不能当作 0 |

只需复核池内候选，可以分批补标；但某次排名的未标注结果必须补齐后才能进入主要指标比较。重要/争议样本建议由第二人复核，`reviewer/reason` 记录最终人工决定；工具不伪造第二位标注者或一致性统计。

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.cli freeze --pool work/live-pool-v1/pool.json --annotations work/live-pool-v1/annotations.csv --version human-parent-v1 --output work/human-parent-v1.json
```

freeze 校验 pool_hash、候选身份、重复标注和人工字段，保存版本、语料/池哈希、阈值、审核记录及 dataset_hash，使用独占创建防止覆盖。补标后另存新版本。形式校验无法证明 reviewer 真的是人，审核流程仍由项目使用者负责。

## 指标与未知标签

默认阈值为 grade>=1，可配置为 2。按单位 ID 去重后取前 K：Recall@5、Precision@5、MRR@5、nDCG@5、Hit@1。Precision 固定除以 K；nDCG 使用 `gain=2^grade-1` 与 `log2(rank+1)` 折扣。没有达到相关性阈值的标签时Recall/MRR为null；所有标注等级收益均为0时nDCG为null，不伪造满分。nDCG独立保留0/1/2分级收益，即使二值阈值为2，grade=1仍贡献部分收益。没有返回结果但有正标签时为0。

发现未标注结果时返回 `needs_judgment`、unjudged_ids 和覆盖率，主指标为 null；整个实验 `comparable=false`，evaluate 以退出码 2 提示补标。Recall 始终标记 `judged_pool_approximation`，不是全库真实召回率。

评测排名格式：

```json
{"corpus_hash":"同一快照哈希","granularity":"parent","rankings":{"query_id":["parent_id_1","parent_id_2"]}}
```

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.cli evaluate --dataset work/human-parent-v1.json --run work/candidate-run.json --k 5 --output work/candidate-eval.json
```

必须包含固定数据集全部查询，空结果使用空列表。新检索器返回池外文档时，扩池并人工补标后发布新数据集版本，不能默认判 0 或拿新版本与旧版本直接对比。

## 拒答、技术失败与诊断质量

自适应脚本的拒答只表示检索 low confidence，不能等同 Agent 正确弃权。`reranker_used` 是尝试使用，新增 `reranker_succeeded` 表示实际完成评分；检索/重排错误单列，不能被记成正确拒答。固定对照中的不可用重排模式输出 null 和 available_queries。

`eval_runner` 独立调用真实 Agent 和 Judge；合成评测禁用现场 MCP、API 日志、Planner 记忆和 Incident 写入，知识来源排除 incident。运行报告记录输入、标签、文件语料、模型和检索配置指纹；这些变化或质量指标缺失会阻止回归门禁通过。此语料指纹不能替代部署快照校验；正式检索比较应使用上述固定 corpus/qrels。

本轮已生成的 1266 个候选全部保持未标注。实际测试和旧/新指标口径对照见 [验收报告](aiops-improvement-20261020.md)，未给新人工标注集编造分数。
