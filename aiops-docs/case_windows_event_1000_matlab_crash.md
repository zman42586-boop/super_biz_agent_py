# 典型案例：Windows Event ID 1000 记录 MATLAB 崩溃

> 类型：Microsoft 官方故障流程归纳（非本项目真实事故）
> 标签：Windows Event Viewer、Event ID 1000、faulting module、MATLAB.exe

## 现象

MATLAB 直接退出但应用目录没有足够 crash dump。Windows 应用程序日志中的 Event ID 1000 可能记录 Faulting application、Faulting module、Exception code 和 offset。

## 排查与处理

按告警时间、PID 和 MATLAB.exe 过滤事件，保存完整 XML；将故障模块与 MATLAB crash dump 调用栈交叉验证。第三方 DLL、MEX 或显卡驱动只应被列为嫌疑项，不能仅凭模块名直接定根因。

## 来源

- https://learn.microsoft.com/en-us/troubleshoot/windows-server/performance/troubleshoot-application-service-crashing-behavior
