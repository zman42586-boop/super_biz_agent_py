# 典型案例：并行任务因 JobStorage 权限长期排队

> 类型：官方故障模式归纳（非本项目真实事故）
> 标签：queued、JobStorageLocation、permission、scheduler

## 现象

任务长期处于 queued，worker 无法读取函数或保存结果，日志出现 permission denied、file not found 或启动失败。

## 排查与处理

确认 worker 实际运行用户、当前目录、JobStorageLocation 读写权限和网络共享可见性。Windows 服务账户通常不能使用交互用户映射的网络盘，应使用可访问的 UNC 路径或正确配置服务账户。函数不可见时检查 AdditionalPaths 或 AttachedFiles。

## 来源

- https://www.mathworks.com/help/parallel-computing/troubleshooting-and-debugging.html
