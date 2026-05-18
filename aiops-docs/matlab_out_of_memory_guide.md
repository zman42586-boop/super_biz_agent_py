# Matlab Out Of Memory Guide

> 来源: (内置运维知识)
> 生成时间: 2026-05-18 15:16:46
> 用途: SuperBizAgent RAG 知识库 — MATLAB 运维诊断参考

---

# MATLAB Out of Memory (OOM) 排查与修复指南

## 概述

MATLAB 报 `Out of memory` 时，表示请求的内存超过了可用 RAM + swap 的连续可用空间。
长时间仿真中，内存泄漏累积最终导致 OOM，系统可能杀掉 MATLAB 进程。

## 常见原因

1. **大数组操作** — 一次性创建超大矩阵
2. **内存泄漏** — 循环中不断 append 数组、不清除临时变量
3. **碎片化** — 运行很久后内存碎片化，没有足够大的连续块
4. **parfor 内存膨胀** — 每个 worker 拷贝数据，N 倍内存消耗
5. **图形对象泄漏** — 不断创建 figure 但未关闭

## 诊断步骤

### 1. 检查当前内存使用
```matlab
% 查看 MATLAB 内存使用
memory                  % Windows 专用
[userview, sysview] = memory;
fprintf('MATLAB 可用: %.1f GB\n', userview.MaxPossibleArrayBytes / 1e9);

% 查看工作区最大变量
whos_vars = whos;
[~, idx] = sort([whos_vars.bytes], 'descend');
for i = 1:min(5, length(idx))
    v = whos_vars(idx(i));
    fprintf('  %s: %.1f MB\n', v.name, v.bytes / 1e6);
end
```

### 2. 监控内存增长
```matlab
% 在循环中加内存检查
for iter = 1:N
    % 业务代码
    if mod(iter, 100) == 0
        [~, sys] = memory;
        avail_gb = sys.PhysicalMemory.Available / 1e9;
        if avail_gb < 2
            warning('内存不足！迭代 %d，剩余 %.1f GB', iter, avail_gb);
        end
    end
end
```

### 3. 找泄漏源
profiler 配合 memory 快照对比，定位哪些变量在持续增长。

## 修复方案

### 方案 A: 减小数组/分块处理
```matlab
% 危险：一次性 8GB
A = randn(100000, 10000);

% 安全：分块处理
BLOCK = 1000;
result = zeros(100000, 1);
for k = 1:BLOCK:100000
    idx = k:min(k+BLOCK-1, 100000);
    result(idx) = sum(randn(length(idx), 10000), 2);
end
```

### 方案 B: 使用 tall arrays
```matlab
ds = datastore('large_data.csv');
t = tall(ds);
% MATLAB 自动分块，不会一次性加载
result = gather(mean(t.Var1));
```

### 方案 C: 及时释放变量
```matlab
% 循环中清除不需要的临时变量
for i = 1:N
    tmp = heavy_computation(i);
    results(:,i) = tmp;
    clear tmp;  % 立即释放
end
```

### 方案 D: parfor 降低内存
```matlab
% 避免每个 worker 都拷贝大数据
shared_data = parallel.pool.Constant(large_readonly_data);
parfor i = 1:N
    data = shared_data.Value;  % 所有 worker 共享
    result(i) = process(data, i);
end
```

## 预防

1. 仿真前估算内存需求: `元素数 × 8 bytes × (中间变量倍数)`
2. 长时间循环中加 periodic `clear` 和内存检查
3. 优先用 `single` 而非 `double` 精度（省一半内存）
4. 稀疏矩阵: `sparse()` 对多数元素为 0 的矩阵

