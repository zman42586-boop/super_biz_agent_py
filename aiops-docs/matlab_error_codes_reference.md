# Matlab Error Codes Reference

> 来源: (内置运维知识)
> 生成时间: 2026-05-18 15:16:46
> 用途: SuperBizAgent RAG 知识库 — MATLAB 运维诊断参考

---

# MATLAB 常见错误码速查表

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

