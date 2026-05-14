---
doc_type: middleware
title: Redis 缓存击穿排查指南
filename: middleware_redis_cache_breakdown.md
severity: warning
related_services:
  - order-service
related_tools:
  - query_logs
  - query_cpu_metrics
  - query_memory_metrics
  - get_service_info
  - get_current_time
tags:
  - middleware
  - redis
  - order-service
updated_at: 2026-05-08
source: simulated_oncall_knowledge_base
---

# Redis 缓存击穿排查指南

## 1. 背景说明

Redis 是 order-service 业务链路中的关键依赖。当 缓存命中率骤降或热点 key 失效 出现时，业务服务可能表现为接口响应变慢、错误率升高、线程池堆积、CPU 升高或内存压力增加。AIOps 诊断时不能只看服务自身指标，还要把中间件状态和服务日志进行关联。

## 2. 典型表现

典型现象包括：接口 P95 响应时间升高、日志中出现 timeout 或 retry、部分请求失败但服务实例仍然存活、CPU 或内存指标出现同步波动。如果 gateway-service 出现大量 5xx，而 order-service、payment-service 或 inventory-service 同时出现下游调用异常，应优先怀疑共享依赖或中间件故障。

## 3. 排查步骤

第一步，调用 `get_current_time` 确定故障时间窗口，避免分析错误时间段。第二步，调用 `get_service_info` 查看受影响服务的依赖信息、实例列表和最近发布记录。第三步，调用 `query_logs` 检索关键词：timeout、connection pool、slow query、lag、refused、reset、retry、circuit、degrade。第四步，调用 `query_cpu_metrics` 与 `query_memory_metrics` 判断异常是否由资源瓶颈放大。

## 4. 关键判断点

重点确认是否存在热点 key 同时过期、大量不存在 key 被访问或缓存重建没有互斥保护。如果异常只发生在某个服务，应优先检查该服务调用中间件的配置、连接池和请求模式；如果多个服务同时异常，则应优先判断中间件整体状态或网络链路问题。对于偶发超时，需要结合重试次数和请求高峰判断是否存在雪崩风险。

## 5. 应急措施

对热点 key 添加互斥重建、本地缓存和随机过期时间，必要时临时限流相关接口。应急过程中需要避免盲目重启所有服务，因为重启可能导致缓存失效、连接风暴或消息重复消费。建议先隔离异常实例、降低流量、限制非核心功能，再逐步恢复。

## 6. 恢复验证

恢复后应检查业务错误率、调用耗时、队列积压、CPU 和内存曲线是否回到基线。`query_logs` 不应再出现大量同类异常；`query_cpu_metrics` 和 `query_memory_metrics` 应显示资源占用稳定。最终报告需要说明中间件异常与业务影响之间的证据链。

## 7. AIOps 使用建议

当 Planner 识别到 Redis 相关关键词时，应同时检索服务画像、对应 runbook 和历史 incident。Executor 每次工具调用后只保留摘要，原始结果落盘，避免把大量日志直接放入上下文。

本文档为智能 OnCall 项目的模拟运维知识库内容，用于验证 RAG 检索、AIOps 诊断、工具调用编排和诊断报告生成流程。实际生产环境应结合真实监控平台、日志平台、CMDB、发布系统和工单系统进行校准。

## 8. 检索与演示建议

在本地知识库演示时，可以将本文档与同类 runbook、服务画像、middleware 文档和历史 incident 一起检索。建议测试问题中显式包含服务名、告警名、异常指标和时间范围，例如“order-service 最近三十分钟 CPU 持续高于 90%，日志出现 slow query，如何排查”。这样可以验证向量检索是否能命中正确主题，也能观察 AIOps 是否会优先调用 `query_logs`、`query_cpu_metrics`、`query_memory_metrics` 和 `get_service_info`。如果召回结果过于分散，应检查标题、frontmatter 标签和正文关键词是否足够区分。若召回内容重复度较高，可以减少模板化表述，补充更具体的故障现象、服务依赖和处理动作。

## 9. 数据质量校验

为了让诊断结果更可信，工具返回的数据需要满足时间窗口一致、服务名一致和指标口径一致三个条件。若日志时间与监控时间不一致，应优先重新查询；若服务名缺失，应通过 `get_service_info` 补充实例和路由信息；若指标只覆盖单个实例，应在报告中注明样本范围。AIOps 不应把单条日志直接当作根因，而应结合趋势、比例、影响范围和处理后的恢复效果形成判断。
