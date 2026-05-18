# Matlab Access Violation Guide

> 来源: (内置运维知识)
> 生成时间: 2026-05-18 15:16:46
> 用途: SuperBizAgent RAG 知识库 — MATLAB 运维诊断参考

---

# MATLAB Access Violation 排查与修复指南

## 概述

Access Violation (访问违规) 是 MATLAB 第二常见的崩溃类型。进程试图访问不属于
它的内存地址（NULL、已释放、越界）。常见于 MEX 文件 bug、数组越界、损坏的 MAT 文件。

## 在崩溃日志中的表现

崩溃日志包含 `Access Violation` 或 `Segmentation Violation` 关键字。
注意查看 Fault Address：
- 0x00000000 → NULL 指针解引用
- 其他地址 → 野指针或越界读写

## 常见原因

1. **数组越界** — `A(end+100)` 或 `A(0)`
2. **野指针** — 引用了已清除的图形对象 (`delete(h); get(h, 'prop')`)
3. **MEX 内存管理错误** — C/C++ MEX 中 `mxFree` 后继续使用
4. **损坏的 MAT 文件** — 文件不完整或版本不兼容
5. **第三方工具箱冲突** — 不兼容的 MEX 扩展在同一进程

## 诊断步骤

### 1. 确定崩溃范围
- 只在特定脚本崩溃 → 脚本有问题
- 启动就崩溃 → 路径/工具箱冲突
- 随机崩溃 → MEX 内存 bug（最难排查）

### 2. 隔离测试
```matlab
% 逐步缩小范围
matlab -nojvm           # 排除 Java 相关
matlab -nosplash -nodesktop  # 排除 GUI 相关
restoredefaultpath;     # 排除第三方工具箱
```

### 3. 检查 MAT 文件
```matlab
% 验证 MAT 文件完整性
try
    data = load('suspicious.mat');
catch ME
    warning('文件可能损坏: %s', ME.message);
end
% 重新生成
save('suspicious.mat', 'data', '-v7');
```

### 4. 定位 MEX 崩溃源
崩溃日志调用栈中 .mexw64 文件 + 偏移 → 用 addr2line 或 dumpbin 定位 C 代码行。

## 修复方案

1. **数组边界检查**: 访问前检查 `if idx > numel(A), error(...); end`
2. **句柄有效性**: 使用 `isvalid(h)` 检查图形/文件对象
3. **清理路径**: `restoredefaultpath; rehash toolboxcache;`
4. **重新生成 MAT**: 不加载来源不明的 MAT 文件
5. **禁用可疑工具箱**: 逐个注释 `addpath` 排查

