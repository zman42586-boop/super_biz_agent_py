# 安全 MATLAB 故障样本

更新：2026-10-20。`safe_cases.json` 是设计清单，默认 `designed_not_executed`，不能当成已发生的真实故障。

| case_id | 注入方式 | 类别 |
|---|---|---|
| recursion_depth | 独立测试进程降低递归限制，有限深度递归并恢复设置 | MATLAB 语言错误 |
| missing_file | 在临时目录读取确定不存在的文件 | MATLAB 语言错误 |
| matrix_dimensions | 2x3 与4x2小矩阵相乘 | MATLAB 语言错误 |
| allocation_failure_simulated | 显式 AIOpsTest:SimulatedAllocationFailure，不分配大内存 | 模拟错误，不是真实 OOM |
| task_deadline | 自带五秒期限的循环 | 有界任务超时，不是进程崩溃 |

仅预览，不启动 MATLAB：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_matlab_case matrix_dimensions
```

具备独立 MATLAB 测试条件后，可显式执行一个白名单案例：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_matlab_case matrix_dimensions --execute --matlab "C:\Program Files\MATLAB\R2026a\bin\matlab.exe" --output work/matrix-observed.json
```

只启动本次 runner 的子进程，以临时目录运行；不 attach、杀死或修改现有 MATLAB 仿真。runner 最多等待清单规定的20秒，包括 MATLAB 启动；慢机器可能在脚本执行前超时，需要人工区分。子进程超时不能被解释为 MATLAB 崩溃。未知非零退出码仅标记 process_exit_unclassified，不能自动断言 native crash。

案例字段包括 case_id、注入方法、设计根因、实际观测、日志/指标/Dump、证据 ID 和知识标注。执行结果保存真实 stdout/stderr、退出码与异常输出，标记 observed_requires_review；没有附加指标采样器或二进制 Dump 采集器，metrics/dump 缺失不能伪造。日志 ID 是本地测试 artifact 标识，进入正式 Agent 评测时还需按 Evidence 协议导入，不能冒充线上 host_evidence 主键。

真实内存耗尽、MEX native crash 和主机故障不在可执行白名单。本轮没有启动 MATLAB，也没有改动真实仿真。需要后续人工在安全环境执行、确认实际根因，再补相关知识标注。
