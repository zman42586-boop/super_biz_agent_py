# 典型案例：并行 worker 内存叠加导致 OOM

> 类型：官方故障模式归纳（非本项目真实事故）
> 标签：parfor、worker memory、OOM、data copy

## 现象

串行运行正常，增加 worker 后系统内存迅速耗尽，部分 worker 被操作系统终止。原因常是每个进程 worker 都复制大变量或创建同规模临时数组。

## 排查与处理

先缩小 worker 数量和输入规模，测量客户端与 worker 的数据传输，并在串行路径用 `whos` 检查临时变量。避免把大只读数据反复广播；根据数据规模选择 `parallel.pool.Constant`、datastore、tall 或 distributed 数据。增加 worker 不等于增加可用内存。

## 来源

- https://www.mathworks.com/help/parallel-computing/resolve-error-client-lost-connection-to-worker.html
- https://www.mathworks.com/help/parallel-computing/choose-how-to-manage-data-in-parallel-computing.html
