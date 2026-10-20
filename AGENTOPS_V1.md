# AgentOps：质量、可靠性与可比较实验

更新：2026-10-20。

## 三类结果必须分开

1. 检索质量：固定语料、查询和人工 qrels，评估 Recall/Precision/MRR/nDCG/Hit。
2. 诊断质量：候选结论与本次事实是否相符，引用是否支持；Verifier 仅是自动检查，还需人工评测。
3. 运行可靠性：Run/工具成功率、延迟、重试、LoopGuard、租约恢复和通知状态。

`succeeded` 只说明执行结束；SUPPORTED 只说明当前候选通过验证。root_cause=null 仍是弃权，不能计作确认根因。相同 LLM 生成和验证有相关性偏差，独立 Judge 也不等于人工真值。

## 离线数据

保留8个合成诊断场景，以及40条旧检索查询和10条域外查询。OOM/高内存场景已收紧可观测边界，不再要求模型凭一次快照确认泄漏或 OOM Killer。场景附 synthetic_fixture 来源标记，不冒充真实 MATLAB 故障注入。

新增 Pooling → 人工0/1/2标注 → 冻结版本 → 同快照评测。新查询文件当前全部是 development，没有声称构建了独立盲测集。方法与 CLI 见 [检索评测说明](docs/retrieval-evaluation.md)。

## 实验报告与门禁

`eval_runner` schema_version=2，记录场景输入与标签哈希、语料文件哈希、模型/检索配置及报告/验证快照。合成诊断模式禁用现场工具、Planner记忆与Incident写入，排除incident知识来源，避免读取无关主机数据或把测试报告再次学入知识库。

```powershell
.\.venv\Scripts\python.exe -m tests.eval_runner --experiment baseline --output work/baseline.json
.\.venv\Scripts\python.exe -m tests.eval_runner --experiment candidate --output work/candidate.json --baseline work/baseline.json --fail-on-regression
```

比较要求场景数量和ID、输入/标签指纹、schema及评测配置一致。Judge/Recall/MRR缺失时门禁失败；不能因服务不可用而忽略样本后声称通过。默认Judge均分不能下降，Recall/MRR最多下降2个百分点，单场景Judge最多下降1分。改变数据集、模型或语料后需要新基线，不能拿历史README数字直接声称提升。

固定检索脚本仍会枚举重排权重，属于开发实验，不能把事后选优当成独立留出集结果。新正式标注评测以 corpus_hash/dataset_hash/granularity/threshold 固定协议。

## 线上指标与人工反馈

保留：

```text
GET  /api/runs/metrics/summary?window_hours=24&kind=alert_diagnosis
GET  /api/runs/{run_id}/evaluations
POST /api/runs/{run_id}/evaluations
```

反馈可记录 root_cause_accuracy、evidence_quality、actionability、hit_at_1；保存 evaluator_name 和人工意见。没有评分返回null，不能当作0分或满分。现有接口可承载人工复核，但本轮没有新增专门的标注Web界面或自动统计跨标注者一致性。

本轮真实测试、旧/新指标口径对照和未完成的人工标注见 [验收报告](docs/aiops-improvement-20261020.md)。
