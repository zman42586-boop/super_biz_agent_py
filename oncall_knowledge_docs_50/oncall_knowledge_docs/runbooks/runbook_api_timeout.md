---
doc_type: runbook
title: 接口超时告警处理方案
filename: runbook_api_timeout.md
alert_name: ApiTimeout
severity: warning
related_services:
  - gateway-service
related_tools:
  - query_logs
  - query_cpu_metrics
  - query_memory_metrics
  - get_service_info
  - get_current_time
tags:
  - runbook
  - apitimeout
  - gateway-service
updated_at: 2026-05-08
source: simulated_oncall_knowledge_base
---

# 接口超时告警处理方案

## 1. 告警定义

ApiTimeout 告警表示 gateway-service 出现了需要值班人员关注的运行状态异常。通常当监控系统在连续多个采样周期内检测到 大量请求达到超时阈值后失败 时，会将事件标记为 warning 或 critical。AIOps 诊断流程应首先通过 `get_current_time` 确认当前时间窗口，再使用 `get_service_info` 获取服务实例、依赖关系和最近变更信息，避免把历史残留指标误判为当前故障。

## 2. 影响范围

该类告警可能影响用户下单、支付跳转、库存扣减或网关转发等核心链路。对于 gateway-service，需要重点关注调用量、错误率、P95 响应时间、实例状态以及上下游依赖。如果异常发生在业务高峰期，即使单个指标尚未达到 critical，也应按潜在故障处理，因为排队、重试和连接池占用可能会在数分钟内放大影响。

## 3. 标准排查步骤

第一步，调用 `get_service_info` 确认 gateway-service 的实例数量、部署版本、上游调用方和下游依赖。如果最近 30 分钟有发布、扩容、配置变更或流量切换，应优先纳入分析。

第二步，调用 `query_cpu_metrics` 查看最近 30 到 60 分钟 CPU 曲线，判断异常是单实例突刺还是全局升高。单实例异常更可能与热点请求、线程卡死或本地资源问题有关；全局异常更可能与流量突增、慢 SQL、缓存失效或下游超时有关。

第三步，调用 `query_memory_metrics` 检查内存、堆使用率和 GC 趋势。如果 CPU 升高同时伴随内存持续上涨，需要考虑对象堆积、频繁 GC、批量任务异常或请求结果缓存过大。

第四步，调用 `query_logs` 检索关键词：ERROR、WARN、timeout、slow query、connection refused、thread pool exhausted、retry、degrade。日志分析时应同时观察错误数量和错误类型，不要只看单条报错。

## 4. 常见原因

常见原因包括：下游慢响应、网络抖动、线程池耗尽、重试放大、网关配置不当。如果日志中出现大量超时或重试，应进一步区分是本服务处理慢，还是下游依赖拖慢。如果监控显示 CPU、内存、错误率同时上升，一般优先怀疑流量突增、慢查询、循环重试或线程池堆积。

## 5. 应急处理

按接口和下游服务聚合超时，必要时降级慢依赖或缩短无效重试。应急动作必须遵循“先止血，再定位，再修复”的原则。对于 critical 告警，可以先通过扩容、限流、降级或摘除异常实例降低影响范围，再保留日志和监控快照用于后续复盘。

## 6. 恢复验证

timeout 数量下降，P95 恢复，重试次数回落。恢复后继续观察至少 15 分钟，确认告警不再触发，错误率恢复到正常基线，P95 响应时间稳定，且没有新的下游告警被触发。最终诊断报告需要记录触发时间、影响服务、排查步骤、根因判断、处理动作和预防措施。

## 7. AIOps 提示

Planner 在生成诊断计划时，应优先选择与 ApiTimeout 相关的 runbook、服务画像和历史 incident。Executor 执行工具后，只将结构化摘要写入上下文，完整日志和监控结果应保存为 artifact，避免上下文膨胀。

本文档为智能 OnCall 项目的模拟运维知识库内容，用于验证 RAG 检索、AIOps 诊断、工具调用编排和诊断报告生成流程。实际生产环境应结合真实监控平台、日志平台、CMDB、发布系统和工单系统进行校准。

## 8. 检索与演示建议

在本地知识库演示时，可以将本文档与同类 runbook、服务画像、middleware 文档和历史 incident 一起检索。建议测试问题中显式包含服务名、告警名、异常指标和时间范围，例如“order-service 最近三十分钟 CPU 持续高于 90%，日志出现 slow query，如何排查”。这样可以验证向量检索是否能命中正确主题，也能观察 AIOps 是否会优先调用 `query_logs`、`query_cpu_metrics`、`query_memory_metrics` 和 `get_service_info`。如果召回结果过于分散，应检查标题、frontmatter 标签和正文关键词是否足够区分。若召回内容重复度较高，可以减少模板化表述，补充更具体的故障现象、服务依赖和处理动作。
