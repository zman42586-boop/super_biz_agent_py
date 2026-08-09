# 典型案例：并行计算被数据传输拖慢

> 类型：官方性能案例归纳（非本项目真实事故）
> 标签：parallel、data transfer、ticBytes、tocBytes、slow

## 现象

CPU 利用率不均、worker 等待明显，`parfor` 比串行更慢。实际耗时集中在客户端向 worker 复制数据或 worker 返回大结果。

## 排查与处理

在缩小数据上使用 Pool Dashboard 或 `ticBytes`/`tocBytes` 估算传输量。大且不常修改的文件放到集群存储，避免每次随任务发送；只传递 worker 需要的字段。区分计算耗时、通信耗时和队列等待后再决定是否增加 worker。

## 来源

- https://www.mathworks.com/help/parallel-computing/choose-how-to-manage-data-in-parallel-computing.html
- https://www.mathworks.com/help/parallel-computing/profile-parallel-code.html
