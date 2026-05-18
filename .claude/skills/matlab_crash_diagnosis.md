---
id: matlab_crash_diagnosis
name: MATLAB 崩溃诊断
description: MATLAB.exe 异常退出（栈溢出/访问违规/OOM等）后的标准化取证分析流程。读取崩溃日志、检查系统状态、检索历史案例、给出修复建议。
---

# MATLAB 崩溃诊断 Skill

适用场景：`lhm_alert_agent` 检测到 MATLAB 进程退出 → AIOps 触发诊断。

## 核心取证链

1. 从告警 evidence 中提取崩溃详情：
   - `crash_type`: 栈溢出 / 访问违规 / OOM / 未知
   - `crash_log`: 崩溃日志原文（含调用栈、出错地址）
   - `monitored_process`: MATLAB.exe
   - `system_snapshot`: 崩溃时 CPU、内存、TOP 进程

2. 使用 **retrieve_knowledge** 查询知识库中的历史类似案例：
   - `crash_type=stack_overflow` → 查 `matlab_stack_overflow_guide.md`
   - `crash_type=access_violation` → 查 `matlab_access_violation_guide.md`
   - `crash_type=out_of_memory` → 查 `matlab_out_of_memory_guide.md`
   - 如果是 MEX 崩溃 → 查 `matlab_mex_crash_guide.md`
   - 所有错误码 → 查 `matlab_error_codes_reference.md`

3. 使用 **search_log**（本地 `logs/app_*.log`）查找崩溃前最近的错误日志证据。

4. 使用 Monitor MCP 的 **query_cpu_metrics**、**query_memory_metrics** 获取当前系统状态做对比（如果系统还活着）。

5. 综合分析生成报告：
   - **根因结论**: 基于崩溃类型 + 调用栈 + 系统快照推断
   - **历史案例参考**: 从知识库召回的相关处理经验
   - **处理建议**: 具体到代码行的修复方案（引用调用栈中出现的函数名）
   - **预防措施**: 基于知识库的长期优化建议

## 分析要点

- 调用栈中同一函数反复出现 → 深度递归 → 建议迭代替代或加深度限制
- 调用栈中含 .mexw64 → MEX 崩溃 → 建议隔离测试 + 逐段注释排查
- 崩溃时内存接近上限 → OOM → 建议分块处理 + 预分配 + tall array
- 崩溃日志中访问地址 0x00000000 → NULL 指针 → 检查句柄有效性

## 规划时请遵守

- 报告中必须引用实际调用栈中的函数名和行号
- 每条处理建议必须基于知识库中检索到的真实经验
- 严禁编造未通过工具获取的数据
