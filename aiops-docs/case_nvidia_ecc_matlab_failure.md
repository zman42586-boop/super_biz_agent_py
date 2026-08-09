# 典型案例：GPU ECC 错误导致 MATLAB 计算失败

> 类型：NVIDIA 官方故障分类归纳（非本项目真实事故）
> 标签：ECC、Xid 48、Xid 63、Xid 64、nvidia-smi、GPU memory

## 现象

MATLAB CUDA 任务出现不可恢复错误，日志包含 Xid 48 或后续 63/64，`nvidia-smi` 显示 ECC 错误或页面退休信息。

## 排查与处理

记录 Xid 序列而不是只看单个编号，保存 ECC 计数和 GPU UUID。按 NVIDIA 指南评估隔离节点、等待任务结束后 reset 或重启；不要通过反复重跑掩盖不可纠正 ECC。此类问题属于 GPU 健康事件，不应归因于 MATLAB 数组代码。

## 来源

- https://docs.nvidia.com/deploy/xid-errors/archive/index.html
- https://docs.nvidia.com/deploy/xid-errors/610/working-with-xid-errors.html
