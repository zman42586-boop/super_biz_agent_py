---
doc_type: sop
title: 紧急回滚 SOP
filename: sop_emergency_rollback.md
severity: info
related_services:
  - order-service
  - payment-service
  - gateway-service
  - inventory-service
related_tools:
  - query_logs
  - query_cpu_metrics
  - query_memory_metrics
  - get_service_info
  - get_current_time
tags:
  - sop
  - sop_emergency_rollback
updated_at: 2026-05-08
source: simulated_oncall_knowledge_base
---

# 紧急回滚 SOP

## 1. 适用场景

本 SOP 适用于智能 OnCall 系统在处理 最近发布后出现错误率升高、接口超时、服务不可用或资源异常 时的标准化操作。SOP 的目标不是替代人工判断，而是帮助值班人员在压力场景下快速完成止血、确认、通知和复盘。AIOps 可以在诊断报告中引用本 SOP，给出符合流程的应急建议。

## 2. 执行前确认

执行前需要使用 `get_current_time` 确认当前时间窗口，并通过 `get_service_info` 查看受影响服务、实例状态、最近发布和依赖关系。对于 order-service、payment-service、gateway-service、inventory-service 这类核心服务，应确认是否处于业务高峰、是否已有其他告警、是否存在正在进行的发布或扩容。

## 3. 操作步骤

第一步，确认最近发布版本和影响服务。第二步，通知相关负责人并冻结非必要变更。第三步，选择稳定版本执行灰度回滚。第四步，观察错误率、响应时间和资源指标。第五步，若回滚无效，继续排查配置、依赖和数据变更。

## 4. 风险控制

回滚可能引入数据结构不兼容、重复消息消费或缓存版本不一致问题，因此需要确认数据库变更是否可逆，必要时只回滚应用层代码。所有应急操作都需要记录执行人、执行时间、目标服务、变更内容和观察结果。对于可能影响用户体验的降级、限流或回滚操作，需要在通知中说明影响范围和预计恢复时间。

## 5. 验证方式

操作完成后，应调用 `query_cpu_metrics`、`query_memory_metrics` 和 `query_logs` 验证恢复效果。恢复不是单个指标下降，而是请求成功率、错误率、响应时间和资源使用率都回到可接受范围。若 15 分钟内告警再次触发，应升级为 incident 并进入复盘流程。

## 6. 沟通模板

建议通知内容包括：故障摘要、影响服务、当前状态、已执行动作、下一步计划和负责人。例如：“gateway-service 出现 5xx 升高，当前已执行限流和下游健康检查，AIOps 初步判断与 inventory-service 超时有关，下一步检查库存服务日志和最近发布。”

## 7. 复盘要求

应急结束后，需要补充事件时间线、根因分析、监控缺口、自动化改进点和长期治理措施。若本次事件由文档缺失或工具结果不足导致诊断延迟，应更新知识库和对应 runbook。

本文档为智能 OnCall 项目的模拟运维知识库内容，用于验证 RAG 检索、AIOps 诊断、工具调用编排和诊断报告生成流程。实际生产环境应结合真实监控平台、日志平台、CMDB、发布系统和工单系统进行校准。

## 8. 检索与演示建议

在本地知识库演示时，可以将本文档与同类 runbook、服务画像、middleware 文档和历史 incident 一起检索。建议测试问题中显式包含服务名、告警名、异常指标和时间范围，例如“order-service 最近三十分钟 CPU 持续高于 90%，日志出现 slow query，如何排查”。这样可以验证向量检索是否能命中正确主题，也能观察 AIOps 是否会优先调用 `query_logs`、`query_cpu_metrics`、`query_memory_metrics` 和 `get_service_info`。如果召回结果过于分散，应检查标题、frontmatter 标签和正文关键词是否足够区分。若召回内容重复度较高，可以减少模板化表述，补充更具体的故障现象、服务依赖和处理动作。

## 9. 数据质量校验

为了让诊断结果更可信，工具返回的数据需要满足时间窗口一致、服务名一致和指标口径一致三个条件。若日志时间与监控时间不一致，应优先重新查询；若服务名缺失，应通过 `get_service_info` 补充实例和路由信息；若指标只覆盖单个实例，应在报告中注明样本范围。AIOps 不应把单条日志直接当作根因，而应结合趋势、比例、影响范围和处理后的恢复效果形成判断。
