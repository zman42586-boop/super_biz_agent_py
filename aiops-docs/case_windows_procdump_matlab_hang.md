# 典型案例：MATLAB 无响应或 CPU 尖峰时抓取 ProcDump

> 类型：Microsoft Sysinternals 官方工具案例（非本项目真实事故）
> 标签：ProcDump、hang、CPU spike、memory threshold、dump

## 现象

MATLAB 进程仍存在，但窗口无响应、CPU 持续高或内存达到特定阈值；普通“进程是否存活”无法识别这种假活状态。

## 排查与处理

ProcDump 可按 CPU 尖峰、窗口挂起、未处理异常或内存 commit 阈值生成转储。先在测试机确认触发条件和磁盘成本，再针对 MATLAB PID 使用。dump 是诊断证据，不应由 Agent 自动上传到外部系统。

## 来源

- https://learn.microsoft.com/en-us/sysinternals/downloads/procdump
