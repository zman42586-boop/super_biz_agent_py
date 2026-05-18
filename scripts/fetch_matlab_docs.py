"""
MATLAB 官方运维文档抓取 + 内置知识库生成

从 MathWorks 帮助中心抓取文档（如可访问），同时生成内置的 MATLAB
运维诊断知识条目。内置条目不依赖网络，覆盖最常见的 MATLAB 崩溃场景。

生成的文档保存到 uploads/ 目录，之后通过 /api/upload 向量化入库。

用法:
    .venv/Scripts/python.exe scripts/fetch_matlab_docs.py
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "aiops-docs"
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_REQUESTS = 3

# ── 在线文档页面（尽力抓取，403 则跳过）──────────────────────────
PAGES = [
    {
        "url": "https://www.mathworks.com/help/matlab/error-and-warning-messages.html",
        "filename": "matlab_error_and_warning_messages.md",
        "topic": "MATLAB 错误与警告消息参考",
    },
    {
        "url": "https://www.mathworks.com/help/matlab/debugging-code.html",
        "filename": "matlab_debugging_code.md",
        "topic": "MATLAB 程序调试指南",
    },
    {
        "url": "https://www.mathworks.com/help/matlab/matlab_prog/strategies-for-efficient-use-of-memory.html",
        "filename": "matlab_memory_management.md",
        "topic": "MATLAB 内存高效使用策略",
    },
    {
        "url": "https://www.mathworks.com/help/matlab/matlab_external/troubleshooting-mex-files.html",
        "filename": "matlab_mex_troubleshooting.md",
        "topic": "MATLAB MEX 文件故障排查",
    },
]

# ── 内置知识条目（不依赖网络，核心诊断参考）───────────────────────
BUILTIN_KNOWLEDGE: dict[str, str] = {}


# ═══════════════════════════════════════════════════════════════════
# 条目 1: 栈溢出
# ═══════════════════════════════════════════════════════════════════
BUILTIN_KNOWLEDGE["matlab_stack_overflow_guide.md"] = r"""# MATLAB 栈溢出 (Stack Overflow) 排查与修复指南

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
"""


# ═══════════════════════════════════════════════════════════════════
# 条目 2: Access Violation
# ═══════════════════════════════════════════════════════════════════
BUILTIN_KNOWLEDGE["matlab_access_violation_guide.md"] = r"""# MATLAB Access Violation 排查与修复指南

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
"""


# ═══════════════════════════════════════════════════════════════════
# 条目 3: Out of Memory
# ═══════════════════════════════════════════════════════════════════
BUILTIN_KNOWLEDGE["matlab_out_of_memory_guide.md"] = r"""# MATLAB Out of Memory (OOM) 排查与修复指南

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
"""


# ═══════════════════════════════════════════════════════════════════
# 条目 4: MEX 崩溃排查
# ═══════════════════════════════════════════════════════════════════
BUILTIN_KNOWLEDGE["matlab_mex_crash_guide.md"] = r"""# MATLAB MEX 文件崩溃排查指南

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
"""


# ═══════════════════════════════════════════════════════════════════
# 条目 5: MATLAB Profiler 性能分析
# ═══════════════════════════════════════════════════════════════════
BUILTIN_KNOWLEDGE["matlab_profiler_guide.md"] = r"""# MATLAB Profiler 性能分析与优化指南

## 概述

MATLAB Profiler 可以统计每行代码的执行时间和调用次数，帮助定位性能瓶颈。
长时间仿真中，一小段低效代码可能累积大量耗时。

## 基本使用

```matlab
profile on                    % 开启 profiler
your_simulation_function();   % 运行仿真
profile viewer                % 查看结果
profile off                   % 关闭
```

## 解读 Profiler 结果

| 列名 | 含义 |
|------|------|
| Calls | 函数被调用次数 |
| Total Time | 该函数的总耗时（含子函数） |
| Self Time | 该函数自身代码耗时（不含子函数） |
| Self Time / Call | 平均每次调用自身耗时 |

关注点：
- High Self Time → 函数本身慢，需要向量化或算法优化
- High Calls → 被大量调用，考虑缓存结果或减少调用频率
- High Total Time but Low Self Time → 瓶颈在子函数

## 常见性能瓶颈与修复

### 1. 循环内动态扩容
```matlab
% 慢：每次迭代都重新分配数组
result = [];
for i = 1:100000
    result(end+1) = compute(i);
end

% 快：预分配
result = zeros(100000, 1);
for i = 1:100000
    result(i) = compute(i);
end
```

### 2. 未向量化操作
```matlab
% 慢：逐元素操作
for i = 1:N
    for j = 1:M
        C(i,j) = A(i,j) + B(i,j);
    end
end

% 快：矩阵运算
C = A + B;
```

### 3. 频繁的文件IO
```matlab
% 慢：每次迭代打开关闭文件
for i = 1:N
    fid = fopen('data.txt', 'a');
    fprintf(fid, '%d\n', values(i));
    fclose(fid);
end

% 快：保持文件打开
fid = fopen('data.txt', 'w');
for i = 1:N
    fprintf(fid, '%d\n', values(i));
end
fclose(fid);
```

## 长时间仿真性能监控

```matlab
% 在仿真主循环中加入性能日志
tic_total = tic;
for iter = 1:N
    tic_iter = tic;
    % 业务代码...
    iter_time = toc(tic_iter);

    if mod(iter, 100) == 0
        elapsed = toc(tic_total);
        fprintf('Iter %d/%d | 本轮 %.2fs | 总计 %.1fmin | 预计剩余 %.1fmin\n', ...
            iter, N, iter_time, elapsed/60, (elapsed/iter * (N-iter))/60);
    end
end
```
"""


# ═══════════════════════════════════════════════════════════════════
# 条目 6: 常见 MATLAB 错误码速查
# ═══════════════════════════════════════════════════════════════════
BUILTIN_KNOWLEDGE["matlab_error_codes_reference.md"] = r"""# MATLAB 常见错误码速查表

## 内存相关

| 错误 | 含义 | 解决方案 |
|------|------|---------|
| `Out of memory` | 请求内存超可用 | 减小数组、分块处理、用 tall array、增加 swap |
| `Maximum variable size exceeded` | 超 MATLAB 单变量上限 | 改用 distributed/tall array |
| `Requested array exceeds maximum array size preference` | 超用户设置上限 | 调整 Preferences → MATLAB → Workspace |

## 计算相关

| 错误 | 含义 | 解决方案 |
|------|------|---------|
| `Index exceeds matrix dimensions` | 索引越界 | 检查数组大小，加边界判断 |
| `Subscript indices must be positive integers` | 索引为0/负/非整数 | 检查 `find()` 结果、确保索引 ≥1 |
| `Matrix dimensions must agree` | 矩阵运算维度不匹配 | 检查 `size()`，用 `bsxfun` 或隐式扩展 |
| `NaN or Inf found` | 数值出现非法结果 | 0/0→NaN, 1/0→Inf，检查输入数据和公式 |
| `Undefined function or variable` | 函数/变量未定义 | 检查拼写、路径、toolbox 是否安装 |

## 文件IO相关

| 错误 | 含义 | 解决方案 |
|------|------|---------|
| `File not found` | 文件不存在 | 检查路径、使用 `exist(filepath, 'file')` |
| `Permission denied` | 无读写权限 | 关闭其他程序对该文件的占用 |
| `Invalid file identifier` | fid 无效 | `fopen` 返回值需要检查 `fid > 0` |
| `End of file` | 读到文件末尾 | `feof(fid)` 检查后再读 |

## 进程崩溃（不显示为异常，直接退出）

| 崩溃类型 | 典型原因 | 诊断方法 |
|---------|---------|---------|
| Stack Overflow | 深度递归 | 查 CrashDumps 目录，找 Stack Overflow 关键字 |
| Access Violation | 野指针/MEX bug | 查 CrashDumps 目录，找 Access Violation 关键字 |
| Out of Memory (crash) | 内存耗尽 | 系统事件查看器，检查 MATLAB 进程退出码 |
| Unexpected Exception | 内部异常 | CrashDumps 中含详细栈和寄存器 |

## 系统与并行

| 错误 | 含义 | 解决方案 |
|------|------|---------|
| `Could not start parallel pool` | parpool 启动失败 | 检查 Parallel Computing Toolbox 许可证 |
| `Timeout waiting for worker` | worker 无响应 | 检查是否有 worker 崩溃/死锁 |
| `Unable to write to preferences file` | 配置文件损坏 | 删除/重命名 `prefdir` 下的 matlabprefs.mat |
"""


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def _clean_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _html_to_markdown(soup: BeautifulSoup, base_url: str) -> str:
    """从 BeautifulSoup 提取主要内容并转为 Markdown 文本。"""
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    main = soup.find("div", class_="content") or soup.find("main") or soup.find("article") or soup
    body = main if main is not soup else soup

    lines: list[str] = []
    for el in body.descendants:
        if el.name is None:
            text = el.strip()
            if text:
                lines.append(text)
            continue

        tag_name = el.name
        if tag_name in ("h1", "h2", "h3", "h4"):
            level = int(tag_name[1])
            text = el.get_text(strip=True)
            if text and len(text) < 300:
                lines.append(f"\n{'#' * level} {text}\n")
        elif tag_name == "p":
            text = el.get_text(strip=True)
            if text and len(text) > 20:
                lines.append(f"\n{text}\n")
        elif tag_name == "li":
            text = el.get_text(strip=True)
            if text and len(text) > 3:
                lines.append(f"- {text}")
        elif tag_name in ("pre", "code"):
            text = el.get_text()
            if text.strip():
                lines.append(f"\n```matlab\n{text.rstrip()}\n```\n")
        elif tag_name == "table":
            lines.append("\n")
            rows = el.find_all("tr")
            for ri, row in enumerate(rows):
                cells = row.find_all(["th", "td"])
                row_text = " | ".join(c.get_text(strip=True) for c in cells)
                lines.append(f"| {row_text} |")
                if ri == 0 and cells:
                    lines.append(f"|{'---|' * len(cells)}")
            lines.append("")

    return _clean_text("\n".join(lines))


def fetch_page(url: str) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    session = requests.Session()
    try:
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"  [SKIP] 获取失败 ({type(e).__name__}): {e}")
        return None


def save_markdown(filepath: Path, title: str, source_url: str, content: str) -> None:
    os.makedirs(filepath.parent, exist_ok=True)
    if not content.strip():
        print(f"  [SKIP] 内容为空")
        return
    md = (
        f"# {title}\n\n"
        f"> 来源: {source_url}\n"
        f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"> 用途: SuperBizAgent RAG 知识库 — MATLAB 运维诊断参考\n\n"
        f"---\n\n"
        f"{content}\n"
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)
    size_kb = len(md.encode("utf-8")) / 1024
    print(f"  已保存: {filepath.name} ({size_kb:.1f} KB)")


def main() -> None:
    print("=" * 60)
    print("MATLAB 运维知识库生成")
    print(f"输出目录: {OUTPUT_DIR}")
    print(f"在线页面: {len(PAGES)} | 内置条目: {len(BUILTIN_KNOWLEDGE)}")
    print("=" * 60)

    total = 0
    success = 0

    # ── 抓取在线页面（优雅跳过 403）──
    for i, page in enumerate(PAGES, 1):
        print(f"\n[{i}/{len(PAGES)}] {page['filename']}")
        html = fetch_page(page["url"])
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        content = _html_to_markdown(soup, page["url"])
        if len(content) < 200:
            content += f"\n\n(自动解析内容有限，完整文档见 {page['url']})\n"
        save_markdown(OUTPUT_DIR / page["filename"], page["topic"], page["url"], content)
        total += 1
        success += 1
        if i < len(PAGES):
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # ── 内置条目（始终成功）──
    print()
    for filename, content in BUILTIN_KNOWLEDGE.items():
        total += 1
        print(f"[内置] {filename}")
        title = filename.replace(".md", "").replace("_", " ").title()
        save_markdown(OUTPUT_DIR / filename, title, "(内置运维知识)", content)
        success += 1

    print()
    print("=" * 60)
    print(f"完成: 生成 {success}/{total} 个文档")
    print(f"输出: {OUTPUT_DIR}")
    print()
    print("上传到向量库:")
    print("  启动服务后，浏览器访问 http://localhost:9900/docs → POST /api/upload")
    print("  或 make upload（会处理 uploads/*.md）")
    print("=" * 60)


if __name__ == "__main__":
    main()
