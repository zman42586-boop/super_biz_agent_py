# Matlab Stack Overflow Guide

> 来源: (内置运维知识)
> 生成时间: 2026-05-18 15:16:46
> 用途: SuperBizAgent RAG 知识库 — MATLAB 运维诊断参考

---

# MATLAB 栈溢出 (Stack Overflow) 排查与修复指南

## 概述

MATLAB 每个函数调用都会在调用栈上分配空间。当递归太深或局部变量过大时，
调用栈耗尽，MATLAB 进程崩溃。这是运行长时间仿真时最常见的崩溃类型。

## 在崩溃日志中的表现

MATLAB 崩溃后在 `%APPDATA%\\MathWorks\\MATLAB\\CrashDumps\\` 生成 dump 文件，
内容包含 `Stack Overflow` 关键字，调用栈中同一函数反复出现。

## 常见原因

1. **无限递归** — 终止条件永远达不到
2. **深度递归** — 算法正确但数据规模超预期（如递归处理大型矩阵）
3. **局部大数组** — 函数内分配了超大局部矩阵（如 `zeros(10000,10000)`）
4. **MEX 内部栈溢出** — C/C++ MEX 函数中栈分配过大
5. **MATLAB 栈空间限制** — 默认栈空间有限，可通过 `feature('SetPrecision')` 等调整

## 诊断步骤

### 1. 确认崩溃类型
打开 CrashDumps 目录，找到最新 dump 文件，搜索 `Stack Overflow` 关键字。

### 2. 查看调用栈定位问题代码
崩溃日志的 Stack Trace 部分：
```
[  0] 0x00000000             matlab.exe+0x00123456
[  1] my_recursive_func     at line 247
[  2] my_recursive_func     at line 248
[  3] my_recursive_func     at line 248
...（重复数百次）
```
重复函数 = 深度递归。第一次出现的位置 → 入口函数。

### 3. 用 dbstack 加深度检查
```matlab
function result = my_recursive_func(n)
    st = dbstack;
    if length(st) > 500
        warning('递归深度 %d，接近栈溢出', length(st));
        error('递归深度超安全限制，中止');
    end
    % 业务逻辑...
end
```

## 修复方案

### 方案 A: 限制递归深度
```matlab
function result = safe_recursive(data, max_depth)
    arguments
        data
        max_depth (1,1) double = 500
    end
    if max_depth <= 0
        error('递归深度超限，请检查输入数据规模或改用迭代算法');
    end
    % 递归调用时递减
    result = safe_recursive(next_data, max_depth - 1);
end
```

### 方案 B: 递归改迭代
```matlab
% 递归版（危险：大 n 会栈溢出）
function r = fib_recurse(n)
    if n <= 2, r = 1; return; end
    r = fib_recurse(n-1) + fib_recurse(n-2);
end

% 迭代版（安全：无栈溢出风险）
function r = fib_iter(n)
    a = 1; b = 1;
    for k = 3:n
        c = a + b; a = b; b = c;
    end
    r = b;
end
```

### 方案 C: 大数组移到堆上
```matlab
% 危险：函数返回时 A 才释放，栈上 800MB+
function bad_example()
    A = zeros(10000, 10000);  % 栈分配
end

% 安全：用 persistent 放堆上
function good_example()
    persistent A
    if isempty(A)
        A = zeros(10000, 10000);  % 堆分配
    end
end
```

## 预防措施

1. 对输入数据规模敏感的函数，加递归深度上限
2. 大数据用 persistent/global 传递，不放在局部变量
3. MEX 文件中用 `mxMalloc` 而非栈分配
4. 仿真前预估递归深度：`depth ≈ log(数据量/最小粒度) / log(分支因子)`

