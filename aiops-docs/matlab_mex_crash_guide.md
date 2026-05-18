# Matlab Mex Crash Guide

> 来源: (内置运维知识)
> 生成时间: 2026-05-18 15:16:46
> 用途: SuperBizAgent RAG 知识库 — MATLAB 运维诊断参考

---

# MATLAB MEX 文件崩溃排查指南

## 概述

MEX 文件 (.mexw64) 是编译后的 C/C++/Fortran 代码，运行在 MATLAB 进程内。
MEX 中的 bug（内存错误、栈溢出）会导致整个 MATLAB 进程崩溃，
而不是抛出可捕获的异常。

## 常见 MEX 崩溃

1. **NULL 指针** — `mxGetPr(plhs[0])` 返回 NULL 后直接写入
2. **越界写入** — 写入超出 `mxGetM` × `mxGetN` 范围
3. **栈溢出** — C 函数内分配大数组: `double buf[10000000];`
4. **内存泄漏** — `mxMalloc` 后不 `mxFree`，长期运行耗尽内存
5. **类型混用** — 把 `mxDOUBLE_CLASS` 当 `mxSINGLE_CLASS` 处理
6. **多线程不安全** — MEX 中使用非线程安全的全局变量

## 诊断步骤

### 1. 崩溃日志分析
MEX 崩溃的 dump 文件中调用栈会包含 .mexw64 文件名 + 偏移量：
```
[ 0] my_mex.mexw64+0x00001234
[ 1] my_mex.mexw64+0x00005678
```
使用 Visual Studio 的 `dumpbin /disasm` 或 `addr2line` 定位崩溃的 C++ 代码行。

### 2. 隔离测试
```matlab
% 用最小输入测试
my_mex(1);           % 标量 → 正常？
my_mex([1 2]);       % 向量 → 崩溃？
my_mex(randn(100));  % 大矩阵 → 崩溃？
```

### 3. 重编译加调试信息
```matlab
% 编译时加 -g 保留符号，崩溃时能看到 C 函数名而非偏移
mex -g my_mex.cpp
```

## 安全编程检查清单

1. 所有 `mxGetPr/mxGetPi/mxGetData` 返回值使用前检查 NULL
2. 写输出前检查 `mxGetM`、`mxGetN` 维度
3. 用 `mxMalloc/mxCalloc` 而非栈分配大数据
4. 配对的 `mxMalloc` ↔ `mxFree`
5. 用 `mxIsDouble/mxIsSingle` 检查输入类型
6. 避免静态/全局变量在多线程 context 中使用
7. 用 `mexPrintf` 而不是 `printf`

## 常用安全模式

```c
#include "mex.h"

void mexFunction(int nlhs, mxArray *plhs[], int nrhs, const mxArray *prhs[]) {
    // 1. 参数数量检查
    if (nrhs < 1) mexErrMsgIdAndTxt("my_mex:nargin", "至少需要一个输入");

    // 2. 类型检查
    if (!mxIsDouble(prhs[0]) || mxIsComplex(prhs[0]))
        mexErrMsgIdAndTxt("my_mex:notDouble", "输入必须是实数 double");

    // 3. 获取指针（检查 NULL — 虽 mexErrMsgIdAndTxt 会中止，但防御性编码）
    double *input = mxGetPr(prhs[0]);
    mwSize M = mxGetM(prhs[0]);
    mwSize N = mxGetN(prhs[0]);

    // 4. 分配输出
    plhs[0] = mxCreateDoubleMatrix(M, N, mxREAL);
    double *output = mxGetPr(plhs[0]);

    // 5. 执行（边界安全）
    for (mwSize i = 0; i < M * N; i++)
        output[i] = input[i] * 2.0;
}
```

