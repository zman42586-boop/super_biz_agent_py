# AIOps 改进核查与验收报告

日期：2026-10-20；本地项目 `super_biz_agent_py`；修改前 Git 基线 `28b4ce2`。本轮开发与验收直接在本地项目完成；提交与同步状态以 Git 记录为准。

## 1. 修改前源码核查

| 已有能力 | 核查到的问题 | 本轮处理 |
|---|---|---|
| Planner–Executor–Microcompact–Replanner | 提示词约束事实/知识/推断，但没有独立验证，Replanner直接发布报告 | 保留架构，增加候选、Verifier和最终报告节点 |
| MySQL Harness、租约、Fencing、Checkpoint | 新节点缺少恢复phase，验证计数/候选无持久化，总超时按认领重开 | JSON状态新增phase/验证历史/指纹/deadline，继续使用原表和Fencing |
| LoopGuard Step/Tool/重复/无进展保护 | Planner预取没有计入总工具预算；no_progress与可修正推理没有区分 | 预取预留、即时会话计数；停止采集仍可有限修正推理 |
| Dense/BM25/RRF/查询扩展/自适应CrossEncoder | 线上Parent排名与文件标签混用，重排尝试失败仍可能被当成成功 | 保留主检索能力，区分attempt/success/error，失败保留RRF并降置信度 |
| 40条查询、10条域外查询、8个合成场景 | relevant_docs无审核记录，文件重复让nDCG超过1，Precision分母不同，description泄漏隐藏根因 | 统一legacy文件级指标；新增独立Pooling/人工标注/冻结评测协议 |
| 工具结果结构化与压缩归档 | 原始Document artifact未贯穿Gateway，最终推理只看摘要可能丢引用 | 压缩前登记原文/位置/版本/哈希，ToolCall和Step保留Evidence |
| 有界主机历史、失联降级 | 普通进程退出未冻结已有历史；search_log是FastAPI日志 | 普通告警同样冻结历史；明确来源和目标主机 |
| Monitor MCP当前psutil值 | 当前值重复填充历史窗口并标记real；service_name未选择主机 | 小范围修正数据语义，只返回当前采样，保留MCP服务 |
| Dump摘要解析 | 文本转大写后与小写关键字比较，类型识别失效；缺少上报来源元数据 | 大小写修正、启动基线、摘要来源/时间/文本哈希/栈传递 |

旧40条查询为8类各5条静态文本，10条域外查询写在脚本中；未发现运行时检索结果反写标签的实现，但没有人工审核和全库相关文档枚举的证据。旧开发/留出只是同类编号划分，不是独立盲测。scenario_03/05原预期会奖励无法由现有快照证明的泄漏/OOM Killer，本轮调整为可观测事实与未知根因。

## 2. 已实施功能与数据结构

- `Evidence`：事实、知识、模型推断分离；保留Run/host/事件与接收时间、来源、快照局部行号定位、原文、哈希及document/version/parent/chunk。
- `CandidateDiagnosis/DiagnosisClaim/Citation`：根因与逐条断言绑定引用；不能把搜索词、模型摘要或历史SOP当作现场事实。
- `VerificationResult`：SUPPORTED/NON_SUPPORTED路由及具体失败原因、缺失/矛盾证据。引用错位给出实际匹配位置。
- State：phase、evidence_registry、diagnosis_context、candidate、verified_fingerprint、验证次数/历史/指纹、prefetch计数、即时会话调用历史、deadline。
- 评测：稳定来源/内容ID、独立候选池、0/1/2人工标注CSV、不可覆盖的数据集版本与哈希、明确的未知标签和近似Recall。
- 安全MATLAB清单：五个可预览案例；默认不执行，无真实OOM、MEX崩溃或主机故障注入。

状态图及JSON字段详细协议见 [Evidence Verifier](evidence-verification.md)。评测构建、标注、冻结及命令见 [检索评测](retrieval-evaluation.md)。MCP架构取舍先形成 [分析报告](mcp-evidence-audit.md)，未删除服务或引入新的网络数据层。

## 3. 单元、集成与真实依赖测试

| 测试 | 实际结果 | 边界 |
|---|---|---|
| 项目完整pytest回归 | **158 passed，2 skipped，21.11秒** | 未设置专用MySQL URL，两个专用用例在该批次跳过；模型边界大多为确定性替换，不能当作诊断准确率 |
| Docker MySQL 8.4独立临时库 | **2 passed，10.09秒** | 强制终止A、B/C接管、旧Token拒绝；Verifier次数/phase/deadline/证据/LoopGuard接管后保留；已清理临时库和用户 |
| 全部本轮Python变更Ruff F检查 | 通过 | 检查未定义变量、未用导入等；项目旧Ruff配置有迁移提示，不影响结果 |
| Python语法编译、git diff --check | 通过 | 没有启动应用或产生真实MATLAB故障 |
| Milvus 2.5.10真实只读检索 | 通过 | 现有biz集合228行，37份来源、202个唯一Parent；没有删除、重建或重索引集合 |
| BGE embedding/CrossEncoder | 真实模型加载并评分成功 | 初始缺少reranker权重，已补齐项目缓存后重跑正式检索评测；初次降级结果不作为最终结果 |
| 真实模型/Judge：栈溢出合成场景 | 首次验证SUPPORTED；Judge **9/10** | 原文/行号支持递归栈耗尽方向；仍不是实机故障注入，不能据此声称真实事故准确率 |
| 真实模型/Judge：高内存无Dump合成场景 | 第二次验证SUPPORTED，root_cause=null；Judge **9/10** | 最终诚实保留原因未确定，没有确认泄漏或OOM Killer；SUPPORTED此时代表安全弃权 |
| MATLAB注入、SMTP交付 | **未执行** | 设计样本不伪装成真实观测；没有启动/终止用户仿真，没有发送实际邮件 |

完整回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts= --no-cov -q -p no:cacheprovider
.\scripts\test_mysql_disposable.ps1
```

项目虚拟环境为Python3.13.13。早期受限执行环境曾出现Python启动/依赖不可用现象；在可访问项目运行时和Docker的环境中，现有依赖已可用，最终完整回归包括tokenizer切分及自适应检索测试。没有替换系统Python或重建用户虚拟环境。

新增用例覆盖：首次通过；失败后同证据修正；补工具证据；无更多证据；Step/Tool/验证预算；重复候选；引用不存在/错位/伪造；来源、Run、主机、时间错配；SOP单独归因；验证中断；phase恢复；总deadline保留；压缩和Tool artifact不丢证据；技术错误不能冒充正确拒答；数据集/语料变更不能通过比较门禁。

## 4. 真实检索结果：旧集合回归口径

以下均在相同已部署集合上实际运行，使用旧预置文件标签，**不是新人工标注集效果，也不是全库真实Recall**。MRR截断到5，Precision固定除以5，文件去重后计分。固定链路会从更大的候选排名中取唯一文件，线上自适应链路只返回5个Parent，因此两张表不应混为同一Top5文件策略。

### 固定链路，40条开发查询

| 方案 | Recall@5 | Precision@5 | MRR@5 | nDCG@5 | Hit@1 |
|---|---:|---:|---:|---:|---:|
| Dense原查询 | 68.33% | 30.00% | 0.8121 | 0.6710 | 75.00% |
| Dense+扩展 | 77.50% | 35.00% | 0.8675 | 0.7614 | 82.50% |
| Dense+BM25+RRF+扩展 | 82.92% | 37.00% | 0.8875 | 0.8091 | 85.00% |
| Hybrid+CrossEncoder，预设权重0.20 | 84.58% | 38.00% | 0.9021 | 0.8210 | 87.50% |

四种模式各40条均可用。Hybrid平均21.61ms，带CrossEncoder平均3224.89ms；这是单次本机开发测量，包含具体模型缓存/CPU条件，不能当作生产延迟保证。枚举其他权重只作开发对照，没有据此更改线上权重或宣称盲测提升。

### 自适应链路，40+10条

| 指标 | 结果 |
|---|---:|
| Recall@5（已列旧文件标签） | 70.83% |
| Precision@5 | 31.50% |
| MRR@5 | 0.8833 |
| nDCG@5 | 0.7407 |
| Hit@1 | 85.00% |
| 域外查询检索低置信度率 | 90.00%（9/10） |
| 可回答查询低置信度率 | 2.50% |
| 查询重试率 | 0.00% |
| 重排尝试率/成功率 | 46.00% / 46.00% |
| 技术错误数 | 0 |
| 平均/P95延迟 | 1.708s / 4.965s |

域外低置信度不是Agent正确拒答率；最终诊断仍需独立验证。旧README的85% Recall、100%拒答没有固定到本次部署快照和新口径，不能直接当作本轮优化前基线。

## 5. 修改前后对照：同一排名重算指标

把本次自适应链路的同一批Parent排名分别交给Git基线的旧指标函数与修正函数，隔离测量逻辑本身的影响：

| 指标 | 旧函数 | 修正函数 |
|---|---:|---:|
| Recall@5 | 70.83% | 70.83% |
| Precision@5 | 74.00% | 31.50% |
| MRR@5 | 0.8771 | 0.8833 |
| nDCG@5 | **1.3367（非法超1）** | **0.7407** |
| Hit@1 | 85.00% | 85.00% |

旧函数把同文件多个Parent重复记为相关命中；MRR变化来自文件级去重后的名次。**这是测量口径修正，不是RAG算法提升或退化的因果证明。** 另外，最小固定输入“同一相关文件5个Parent”的nDCG由2.948459修正为1.0；单条结果Precision由按实际返回数计算改为固定K。

没有同协议、同快照、独立人工标签的诊断前后准确率对照，本轮不编造该数字。两个真实LLM/Judge样本属于冒烟联调，不能替代全量诊断质量评估。

## 6. 人工候选池交付

已对当前部署语料生成真实多路候选池：**50条查询、1266个Parent候选**。Dense和Milvus BM25各Top10，再合并独立BM25、精确标识符和标题/元数据召回。所有grade留空，没有把Reranker分数或LLM判断写成人工Ground Truth。

- corpus_hash：`4a3509ec7c96278b45bf0ccbdbf30453640e3671779aeb21b4b71da3cdc594f7`
- pool_hash：`c66bfc890547cbfd6fc5ac9df7950c92b66772e3426e8ba4ca85e2cd5ecdd3ae`
- 本地待标注入口：[annotations.csv](../work/evaluation-20261020/live-pool/annotations.csv)
- 本地原始池：[pool.json](../work/evaluation-20261020/live-pool/pool.json)

人工填写grade/reviewer/reason后使用freeze发布版本。未标注项仍是unknown；正式人工标注集Recall/Precision等结果当前为**未评测**。不要求遍历全库，也不要求一次完成所有候选。

原始结果保存在被Git忽略的 `work/evaluation-20261020/final/`：adaptive.json、fixed-retrieval.json、metric-correction.json、两个diagnosis-scenario*.json与日志、unit-integration.log。旧降级联调文件留在其父目录，不与final结果混用。work包含运行材料，不提交到代码仓库。

## 7. 已知限制与未实施项

- 同一LLM生成和验证仍可能一起误判。正常内存快照不足以严格排除历史内存问题，栈溢出也不自动排除并发原因；需要人工复核真实报告，不能把Verifier当成因果证明器。
- 二分类用于路由，具体反馈完整保留；失败预算耗尽会放弃候选断言，保留原始事实和待补证据。
- Dump仅摘要/栈、关键日志与两小时有界历史，不能还原未采集数据。无时区记录不能做强UTC匹配；主机身份未升级成每主机独立认证。
- 启动修改时间基线降低旧Dump误关联风险，但还没有严格PID/进程生命周期关联。二进制Dump原文件上传/存储未实现。
- 无标注Web界面、无自动生成的人类真值、无独立盲测集；人工复核可用CSV和已有Run评分API。
- 未运行MATLAB真实注入、完整8场景多次模型评测、线上真实事故准确率或SMTP交付验收。五个安全设计样本和禁用场景清单已提交。
- MCP整体收敛、外部监控平台、全量日志/对象存储、Recovery Executor和自动修复不在本轮实现范围。

## 8. 修改文件清单

下列清单按本地Git差异生成，包含实现、测试、样本、配置和对应文档；不包含.env、模型权重、运行结果或测试临时库。

<!-- generated-file-list -->

- `.env.example`
- `.gitignore`
- `AGENTOPS_V1.md`
- `HARNESS_V1.md`
- `PROJECT_ANALYSIS.md`
- `README.md`
- `app/agent/aiops/evidence.py`
- `app/agent/aiops/executor.py`
- `app/agent/aiops/planner.py`
- `app/agent/aiops/replanner.py`
- `app/agent/aiops/state.py`
- `app/agent/aiops/verifier.py`
- `app/evaluation/cli.py`
- `app/evaluation/experiment.py`
- `app/evaluation/live_pool.py`
- `app/evaluation/pooling.py`
- `app/evaluation/retrieval.py`
- `app/harness/config.py`
- `app/harness/host_report.py`
- `app/harness/repository.py`
- `app/harness/runtime.py`
- `app/harness/tool_gateway.py`
- `app/harness/worker.py`
- `app/models/diagnosis.py`
- `app/services/aiops_service.py`
- `app/services/alert_service.py`
- `app/services/document_identity.py`
- `app/services/document_preprocessing.py`
- `app/services/document_splitter_service.py`
- `app/services/retrieval_context.py`
- `app/services/vector_index_service.py`
- `app/services/vector_store_manager.py`
- `app/tools/host_history.py`
- `app/tools/knowledge_tool.py`
- `app/tools/log_tool.py`
- `docs/aiops-improvement-20261020.md`
- `docs/durable-oncall.md`
- `docs/evidence-verification.md`
- `docs/mcp-evidence-audit.md`
- `docs/retrieval-evaluation.md`
- `docs/system_flow.html`
- `mcp_servers/README.md`
- `mcp_servers/monitor_server.py`
- `scripts/lhm_alert_agent.py`
- `scripts/run_matlab_case.py`
- `scripts/test_mysql_disposable.ps1`
- `tests/eval_adaptive_retrieval.py`
- `tests/eval_judge.py`
- `tests/eval_retrieval.py`
- `tests/eval_retrieval_v2.py`
- `tests/eval_runner.py`
- `tests/eval_scenarios/scenario_01_stack_overflow.json`
- `tests/eval_scenarios/scenario_02_access_violation.json`
- `tests/eval_scenarios/scenario_03_out_of_memory.json`
- `tests/eval_scenarios/scenario_04_mex_crash.json`
- `tests/eval_scenarios/scenario_05_memory_leak.json`
- `tests/eval_scenarios/scenario_06_high_cpu.json`
- `tests/eval_scenarios/scenario_07_process_exit_unknown.json`
- `tests/eval_scenarios/scenario_08_manual_exit.json`
- `tests/fault_injection/README.md`
- `tests/fault_injection/allocation_failure_simulated.m`
- `tests/fault_injection/matrix_dimensions.m`
- `tests/fault_injection/missing_file.m`
- `tests/fault_injection/recursion_depth.m`
- `tests/fault_injection/safe_cases.json`
- `tests/fault_injection/task_deadline.m`
- `tests/retrieval_queries_v3.json`
- `tests/test_adaptive_retrieval.py`
- `tests/test_alert_harness.py`
- `tests/test_document_splitter_v2.py`
- `tests/test_eval_experiment.py`
- `tests/test_evidence_verifier.py`
- `tests/test_fault_evidence_chain.py`
- `tests/test_judged_retrieval.py`
- `tests/test_mysql_worker_recovery.py`
- `tests/test_verifier_graph.py`
