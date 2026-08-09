# 典型案例：NVIDIA Xid 与 MATLAB GPU 任务崩溃

> 类型：NVIDIA 官方故障分类归纳（非本项目真实事故）
> 标签：NVIDIA Xid、CUDA、event log、GPU crash、driver

## 现象

MATLAB GPU 任务退出或 CUDA 调用失败，操作系统事件日志同时出现 NVIDIA Xid。Xid 可能指向驱动、应用命令、PCIe、显存或硬件问题。

## 排查与处理

保存 Xid 编号、时间、GPU UUID、驱动版本和 MATLAB crash dump。Xid 是排查起点，不是唯一根因；按 NVIDIA Debug Guidelines 对应编号采取采集、reset、隔离节点或硬件检查。只有时间和设备一致时才与 MATLAB 事件关联。

## 来源

- https://docs.nvidia.com/deploy/xid-errors/introduction.html
- https://docs.nvidia.com/deploy/gpu-debug-guidelines/
