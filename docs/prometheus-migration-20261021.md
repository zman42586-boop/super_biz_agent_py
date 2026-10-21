# Prometheus 改造验收记录

日期：2026-10-21；修改前Git基线`f862519`；直接在本地项目改造，发布状态以Git记录为准。

## 完成范围

- 用固定版本windows_exporter替代项目CPU/内存直接采样，由本地Prometheus每5秒抓取、默认保留15天。
- 保留MCP服务入口，将本机psutil/LHM工具替换为唯一的`query_prometheus_metrics`，查询Prometheus真实单点与历史。
- 按用户要求移除温度采集、LHM启动/配置/工具和温度心跳字段，不实现其他温度替代方案。
- 原事件代理保留进程名/PID检测、退出事件、Dump解析、独立心跳和关键日志上传。CPU/内存快照仅在事件发生时读取本地Prometheus，不再重复直接采样或持续写MySQL。
- 保留旧CPU/内存心跳兼容；新存活心跳不产生空指标证据。日志、告警上下文、Run、工具结果、证据和通知继续走MySQL Harness。
- 新查询只允许固定指标与显式host_id，限制时区、最大时间跨度、点数；检查up与样本新鲜度，空缺不补零。PromQL求值时间与原始硬件采样时间明确区分。
- 自动诊断任务限制目标主机；失联任务允许独立Prometheus历史，查询截止冻结最后心跳，仍禁止主机实时工具和search_log。工具结果在压缩前逐点登记Evidence，现有Verifier继续校验。
- 增加官方便携二进制下载与SHA256校验、本地配置生成/启动/停止、云端receiver Compose和remote_write配置；运行文件、数据及日志不入Git。
- 同步README、PROJECT_ANALYSIS、HARNESS、AgentOps、MCP、持久化OnCall、证据验证和HTML流程图，新增部署与验收文档。

## 验证

1. 完整回归：`python -m pytest -o addopts= --no-cov -q -p no:cacheprovider`，180 passed、2 skipped，19.69秒。使用已有本地模型缓存及用户启动的真实Milvus；包含真实API毫秒时间截断的回归检查。
2. 真实MySQL：`scripts/test_mysql_disposable.ps1`，2 passed，10.23秒；独立临时数据库/账号已清理，未改动项目数据库。
3. 静态检查：改动Python文件的Ruff F规则和`git diff --check`通过，依赖锁文件已同步。
4. 真实Windows Exporter与Prometheus：windows目标up，CPU idle counter和memory available/physical_total实际存在；CPU rate与内存百分比查询成功。不是用当前值伪造历史。
5. 真实remote_write：第二个原生Prometheus在本机19090模拟云端receiver，实际接收Windows指标。比较双方共有连续历史窗口，CPU/内存各3个求值点一致，允许浮点聚合的1e-9表示误差，不包含同步启用前历史。
6. 真实MCP：FastMCP客户端发现且仅发现`query_prometheus_metrics`，通过HTTP查询真实Prometheus历史成功。再用项目LangChain MCP适配器和ToolGateway实际调用，3个主机匹配的原始指标点成功登记Evidence；不是mock模型或mockHTTP结果。
7. 所有验收用监控进程已停止，数据文件留在忽略目录；本机默认配置重新生成为不启用remote_write。项目原有MySQL/Milvus容器保留运行。

真实API测试发现Prometheus会把求值时间截断到毫秒，而调用方ISO时间可带微秒；已增加仅1毫秒的边界容差，超过范围仍拒绝。查询模块及回环MCP连接不受系统HTTP代理影响。此前验收脚本的导入顺序和冷启动比较窗口问题也已修正后重跑；不把失败尝试记成通过。

早期完整回归曾因Docker未启动无法连接Milvus；用户启动后已完整重跑通过。被移除温度功能的旧测试已更新为新行为检查，未通过跳过温度测试来隐藏失败。

## 部署边界

本轮验证在用户Windows本机进行。云端同步使用同机第二个真实Prometheus模拟receiver；用户实际云服务器域名、TLS认证入口和登录信息未提供，因此不宣称已在真实云服务器发布。示例receiver仅监听回环，公网入口需按部署文档配置。

默认无remote_write地址时不发送到外部平台；CPU/内存历史主存储为Prometheus，MySQL保留任务与关键日志。Exporter、Prometheus或网络不可用时数据会缺失，不能据此确定物理宕机。未实际运行MATLAB故障注入、SMTP发信或自动修复。
