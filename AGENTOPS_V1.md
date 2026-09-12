# AgentOps v1: 证明 Agent 优化有效

AgentOps v1 把项目已有的两类数据连成一个闭环：

1. **离线质量评测**：固定 8 个 MATLAB 故障场景，比较相同输入下不同版本的
   Recall@5、Precision@5、MRR 和诊断报告评分。
2. **线上运行监控**：直接聚合 Harness 已持久化的 Run、Step、ToolCall 和 Event，
   观察成功率、P95 延迟、重试率、LoopGuard 触发率和恢复率。
3. **线上质量反馈**：人工、规则或 LLM Judge 可以给真实 Run 写入 0-10 分及子指标，
   让“运行成功”和“诊断正确”分开衡量。

## 为什么不能只看成功率

`succeeded` 只代表 Agent 正常生成了报告，不代表根因正确。判断优化是否有效，需要同时满足：

- **质量不下降**：根因准确性、证据充分性、建议可执行性、Recall@K、MRR。
- **可靠性改善**：Run 成功率上升，工具失败与异常循环减少。
- **性能成本可接受**：P50/P95 延迟、平均步骤数、工具调用数、重试率没有明显恶化。

因此发布判断应采用多指标门禁，而不是只比较一个总分。例如：

```text
根因准确率不得下降
Recall@5 不得下降超过 2 个百分点
Run 成功率不得下降
P95 延迟不得上升超过 20%
LoopGuard 触发率不得明显上升
```

LLM 输出具有随机性。正式比较时应在同一数据集、相同模型参数下重复运行多次，报告均值、
失败样本和波动范围；LLM-as-Judge 应与确定性规则和人工抽检结合，不能作为唯一真值。

## API

查询最近 24 小时全部 Run：

```http
GET /api/runs/metrics/summary?window_hours=24
```

只查看自动告警诊断：

```http
GET /api/runs/metrics/summary?window_hours=24&kind=alert_diagnosis
```

响应分成三组：

- `runs`：状态分布、成功率、Run 延迟 P50/P95、平均 Step/ToolCall、LoopGuard 触发率、恢复率。
- `tools`：工具成功率、重试率、工具延迟 P50/P95。
- `quality`：已评分 Run 数、平均质量分和通过率。

给一个真实 Run 写入人工或自动评分：

```http
POST /api/runs/{run_id}/evaluations
Content-Type: application/json

{
  "experiment_name": "loop-guard-v1",
  "evaluator_name": "human-review",
  "score": 8.5,
  "passed": true,
  "metrics": {
    "root_cause_accuracy": 0.9,
    "evidence_quality": 0.8,
    "actionability": 0.9
  },
  "comment": "根因和证据正确，处置建议可执行"
}
```

查看该 Run 的所有评分：

```http
GET /api/runs/{run_id}/evaluations
```

评分会写入 MySQL 的 `agent_run_evaluations` 表，并追加 `run_evaluated` 事件。

## 推荐的优化对比流程

1. 固定代码、模型、Prompt、知识库版本，运行一次，命名为 `baseline`。
2. 只修改一个变量，例如 LoopGuard 策略或检索重排策略。
3. 在完全相同的 8 个场景上运行 `candidate`，必要时每个场景重复 3 次。
4. 对比聚合指标，并逐个检查退化样本，而不是只看平均分。
5. 达到门禁后再上线；上线后用 `/api/runs/metrics/summary` 观察真实流量。
6. 将线上失败 Run 脱敏后加入离线场景，形成下一轮回归集。

可直接生成并比较机器可读的实验报告：

```powershell
.\.venv\Scripts\python.exe -m tests.eval_runner `
  --experiment baseline `
  --output eval-reports\baseline.json

.\.venv\Scripts\python.exe -m tests.eval_runner `
  --experiment loop-guard-v1 `
  --output eval-reports\loop-guard-v1.json `
  --baseline eval-reports\baseline.json `
  --fail-on-regression
```

门禁默认要求：Judge 平均分不下降、Recall@K/MRR 下降不超过 2 个百分点，且任一场景
Judge 分数不能下降超过 1 分。数据集数量或场景 ID 变化时拒绝比较，避免拿不同题目制造
“优化后更好”的假象。

当前版本直接利用 MySQL，适合校招项目和单机演示。后续接入 Prometheus/OpenTelemetry 时，
可以把相同字段导出到监控系统，而不需要修改指标定义。
