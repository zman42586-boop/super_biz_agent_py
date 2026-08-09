# 典型案例：用 Windows WER 捕获 MATLAB 完整进程转储

> 类型：Microsoft 官方故障流程归纳（非本项目真实事故）
> 标签：WER、LocalDumps、full dump、MATLAB.exe、WinDbg

## 现象

MATLAB 闪退可复现，但自身 crash dump 缺失或信息不足，需要在异常发生时保留完整进程状态。

## 排查与处理

由管理员按 Microsoft LocalDumps 文档只为 `MATLAB.exe` 配置转储目录、数量和 DumpType，确认磁盘容量后复现。使用 WinDbg `!analyze` 或交给支持团队分析。完成后删除对应注册表配置，避免持续生成大文件。转储可能包含业务数据，传输前必须脱敏和授权。

## 来源

- https://learn.microsoft.com/en-us/troubleshoot/windows-server/performance/troubleshoot-application-service-crashing-behavior
