# 典型案例：Tall 计算最后 gather 再次 OOM

> 类型：官方大数据工作流归纳（非本项目真实事故）
> 标签：tall、gather、lazy evaluation、OOM

## 现象

tall 计算阶段内存稳定，但执行 `gather` 时内存耗尽。原因是 gather 要把最终结果收集到内存；如果结果本身仍然很大，tall 不能消除这个物理限制。

## 排查与处理

在 gather 前先通过过滤、聚合、降采样减少结果，或将中间结果写入 datastore。检查是否意外 gather 了原始明细而不是汇总。tall 的优势是分块和延迟执行，不代表任意大小结果都能放进内存。

## 来源

- https://www.mathworks.com/help/matlab/ref/tall.tall.html
- https://www.mathworks.com/help/matlab/tall-arrays.html
