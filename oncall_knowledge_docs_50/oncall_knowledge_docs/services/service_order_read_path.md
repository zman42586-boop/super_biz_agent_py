---
doc_type: service
title: order-service 服务画像与 OnCall 排查指南
filename: service_order_read_path.md
severity: info
related_services:
  - order-service
  - gateway-service
  - payment-service
related_tools:
  - query_logs
  - query_cpu_metrics
  - query_memory_metrics
  - get_service_info
  - get_current_time
tags:
  - service
  - order_service
updated_at: 2026-05-08
source: simulated_oncall_knowledge_base
---

# order-service 服务画像与 OnCall 排查指南

## 1. 服务定位

order-service 是智能 OnCall 演示环境中的核心业务服务之一，主要负责订单读路径、订单列表查询、订单详情聚合和用户订单历史展示。在故障诊断中，服务画像用于帮助 AIOps 判断告警影响范围、上下游依赖和优先排查方向。Planner 在接收到某个服务相关的告警后，应优先检索本服务文档，再结合对应 runbook 和历史 incident 制定排查计划。

## 2. 上下游关系

该服务的主要依赖包括：gateway-service、payment-service。其中上游通常通过 `gateway-service` 进入业务链路，下游可能涉及数据库、缓存、消息队列或其他业务服务。使用 `get_service_info` 可以查询服务实例、部署版本、依赖关系、负责人和最近变更记录。若某次异常同时影响多个服务，应先判断是网关层问题、共享中间件问题，还是单个服务故障扩散。

## 3. 核心监控指标

OnCall 诊断时需要关注以下指标：请求量、错误率、P95/P99 响应时间、CPU 使用率、内存使用率、实例健康状态、线程池队列长度、下游调用耗时和重试次数。`query_cpu_metrics` 用于判断计算资源压力，`query_memory_metrics` 用于判断堆积、泄漏或 GC 压力，`query_logs` 用于确认异常类型和业务错误上下文。

## 4. 常见故障模式

order-service 的常见风险包括：分页查询过深；索引缺失导致慢查询；缓存失效导致读流量打到数据库；大用户订单列表拖慢接口。当出现接口超时或错误率升高时，不应只看本服务日志，还应关联上下游调用关系。例如 order-service 的创建订单失败，可能来自库存扣减超时、支付预创建失败或数据库慢查询；gateway-service 的 5xx 增加，也可能只是下游服务不可用的表现。

## 5. 排查建议

第一步，调用 `get_current_time` 和 `get_service_info` 确认当前诊断窗口、服务版本和实例状态。第二步，使用 `query_logs` 检索最近 30 分钟 ERROR、WARN、timeout、retry、degrade 等关键词。第三步，结合 `query_cpu_metrics` 和 `query_memory_metrics` 判断资源瓶颈是否与日志异常一致。第四步，如果发现异常集中在某个实例，应考虑摘除实例或重启；如果所有实例都异常，应优先检查依赖、流量和发布变更。

## 6. 诊断报告输出要求

最终报告应包含：服务名称、告警类型、影响接口、异常时间线、关键证据、可能根因、应急动作、恢复验证和长期优化建议。对于无法完全确认根因的情况，应明确说明证据不足，并给出下一步需要补充的数据，而不是直接给出确定性结论。

## 7. 知识库使用说明

本服务画像适合与 runbook、middleware 和 incident 文档联合检索。AIOps 如果召回了本文件，应将服务依赖关系作为诊断计划的输入，避免只围绕单个指标做孤立分析。

本文档为智能 OnCall 项目的模拟运维知识库内容，用于验证 RAG 检索、AIOps 诊断、工具调用编排和诊断报告生成流程。实际生产环境应结合真实监控平台、日志平台、CMDB、发布系统和工单系统进行校准。

## 8. 检索与演示建议

在本地知识库演示时，可以将本文档与同类 runbook、服务画像、middleware 文档和历史 incident 一起检索。建议测试问题中显式包含服务名、告警名、异常指标和时间范围，例如“order-service 最近三十分钟 CPU 持续高于 90%，日志出现 slow query，如何排查”。这样可以验证向量检索是否能命中正确主题，也能观察 AIOps 是否会优先调用 `query_logs`、`query_cpu_metrics`、`query_memory_metrics` 和 `get_service_info`。如果召回结果过于分散，应检查标题、frontmatter 标签和正文关键词是否足够区分。若召回内容重复度较高，可以减少模板化表述，补充更具体的故障现象、服务依赖和处理动作。
