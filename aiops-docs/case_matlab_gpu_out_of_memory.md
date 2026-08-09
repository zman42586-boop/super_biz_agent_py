# 典型案例：MATLAB GPU 显存耗尽

> 类型：官方 GPU 工作流归纳（非本项目真实事故）
> 标签：gpuArray、AvailableMemory、GPU OOM、gpuDevice、reset

## 现象

系统 RAM 尚有余量，但 gpuArray 或 CUDAKernel 操作因显存不足失败。`gpuDevice` 的 AvailableMemory 持续下降，工作区中可能仍保留 GPU 变量。

## 排查与处理

记录 `gpuDevice` 属性、显存和失败操作，减少批量大小或释放不再使用的 gpuArray。必要时 `reset(gpuDevice)` 可清空设备，但会使现有 gpuArray/CUDAKernel 对象失效，必须重新创建；不要在生产任务中未经确认自动 reset。

## 来源

- https://www.mathworks.com/help/parallel-computing/parallel.gpu.gpudevice.html
- https://www.mathworks.com/help/parallel-computing/parallel.gpu.gpudevice.reset.html
