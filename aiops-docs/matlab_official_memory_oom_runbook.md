# MATLAB 内存占用与 Out of Memory 官方排查 Runbook

> 适用版本：MATLAB R2023b 及兼容版本
> 内容依据：MathWorks 官方文档整理，命令和结论以链接页面为准
> 检索标签：MATLAB、内存、memory、whos、OOM、Out of Memory、预分配、动态扩容、datastore、tall、single、sparse

## 官方来源

- [Resolve "Out of Memory" Errors](https://www.mathworks.com/help/matlab/matlab_prog/resolving-out-of-memory-errors.html)
- [Strategies for Efficient Use of Memory](https://www.mathworks.com/help/matlab/matlab_prog/strategies-for-efficient-use-of-memory.html)
- [memory — Memory information](https://www.mathworks.com/help/matlab/ref/memory.html)
- [Preallocation](https://www.mathworks.com/help/matlab/matlab_prog/preallocating-arrays.html)

## 先判断是哪一类问题

1. `Requested array exceeds the maximum possible variable size`：单次申请的数组过大，或超过数组大小偏好设置。
2. `Out of memory`：MATLAB 无法完成当前内存分配，可能伴随大量分页，严重时 MATLAB 或系统失去响应。
3. MATLAB 进程内存持续增长：优先检查循环中的数组动态扩容、未释放的临时变量、图形对象、缓存，以及并行 worker 对数据的复制。
4. 进程没有 MATLAB crash dump，但退出前系统内存接近耗尽：需要结合操作系统事件日志确认是否被系统终止，不能仅凭“没有 dump”断言根因。

## 诊断步骤

### 1. 保存现场

记录告警时间、MATLAB PID、系统内存使用率、交换空间或页面文件、TOP 进程及最近趋势。不要在保存现场前直接清空工作区。

### 2. 查看 MATLAB 内存与工作区变量

```matlab
% memory 在 Windows 上提供 MATLAB 与系统内存信息
[userView, systemView] = memory;

% 找出工作区中占用最大的变量
vars = whos;
[~, order] = sort([vars.bytes], "descend");
vars(order(1:min(10, numel(order))))
```

`memory` 的结果是调用时刻的快照，会受到硬件和实时系统负载影响。诊断长时间任务时，应周期记录趋势，而不是只看一个采样点。

### 3. 检查动态扩容和临时副本

循环中逐步增长数组会反复查找更大的连续内存块，并在搬移期间同时保留新旧副本。应优先预分配：

```matlab
% 不推荐：循环中动态扩容
x = 0;
for k = 2:1000000
    x(k) = x(k-1) + 5;
end

% 推荐：提前分配最终大小
x = zeros(1, 1000000);
for k = 2:1000000
    x(k) = x(k-1) + 5;
end
```

创建非 `double` 数组时应直接指定类型，避免先创建 `double` 再转换：

```matlab
A = zeros(1000, 1000, "single");
```

### 4. 检查数据类型和存储结构

- `double` 每元素 8 字节，`single` 每元素 4 字节；只有在精度允许时才能转换。
- 稀疏矩阵应考虑 `sparse`，但要确认后续运算支持稀疏存储。
- 大量很小的 cell 或 structure 元素会带来额外头部开销。
- 使用 `fread` 读取二进制数据时，可用 `uint8=>uint8` 等形式避免默认转换为 `double`。

### 5. 数据超过内存时改用分块访问

- CSV、图片或文件集合：使用 `datastore` 分批读取。
- MAT 文件：使用 `matfile` 按索引访问变量的一部分。
- 适用计算：在 `datastore` 上创建 `tall` 数组。
- 已不再使用的大变量：确认现场已保存后再使用 `clear variableName`。

## 处理建议的优先级

1. 修正动态扩容和不必要的临时副本。
2. 只加载当前步骤需要的数据，采用分块计算。
3. 在精度允许时使用更小的数据类型或稀疏存储。
4. 检查并行 worker 的数量及每个 worker 的数据副本。
5. 最后才考虑增加物理内存或调整 MATLAB 设置；关闭数组大小保护可能让 MATLAB 或整机失去响应，不应作为常规修复。
