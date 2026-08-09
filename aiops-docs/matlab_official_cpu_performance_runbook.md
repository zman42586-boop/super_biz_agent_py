# MATLAB CPU 高负载与性能分析官方 Runbook

> 适用版本：MATLAB R2023b 及兼容版本
> 检索标签：MATLAB、CPU、高负载、profile、Profiler、timeit、tic toc、parfor、mpiprofile、负载均衡、性能回归

## 官方来源

- [profile — Profile execution time for functions](https://www.mathworks.com/help/matlab/ref/profile.html)
- [Profile Your Code to Improve Performance](https://www.mathworks.com/help/matlab/matlab_prog/profiling-for-improving-performance.html)
- [Measure the Performance of Your Code](https://www.mathworks.com/help/matlab/matlab_prog/measure-performance-of-your-program.html)
- [Profile Parallel Code](https://www.mathworks.com/help/parallel-computing/profile-parallel-code.html)

## CPU 高不等于故障

MATLAB 仿真、矩阵运算、优化求解和并行循环可能长时间占用 CPU。告警后应先确认任务是否仍有进展，再判断是正常的计算密集型负载、并行负载不均衡，还是死循环或性能回退。

需要同时检查：

- CPU 是否持续高于阈值，以及是否集中在 MATLAB 进程；
- 内存、温度和页面文件是否同步恶化；
- 输出文件、迭代次数或日志时间戳是否仍在变化；
- 相同输入与历史基线相比是否明显变慢；
- 并行 worker 是否存在一部分繁忙、一部分等待的情况。

## 单机代码热点分析

```matlab
profile clear
profile on -history
run("target_script.m")
p = profile("info");
save("profile_result.mat", "p")
```

Profiler 可以记录函数调用次数、总耗时、逐行耗时和调用历史，用来定位 MATLAB 把时间花在哪里。Profiler 本身有开销，所以结果适合比较相对热点，不应当作无扰动的绝对性能数据。

对于可封装成函数的代码，使用 `timeit` 获得更稳健的耗时测量；`tic`/`toc` 适合快速估算一个代码片段：

```matlab
f = @() targetFunction(inputData);
seconds = timeit(f);
```

## 并行任务分析

Parallel Computing Toolbox 可使用 `mpiprofile` 采集各 worker 的函数耗时、通信时间和等待时间：

```matlab
pool = parpool("Processes");
mpiprofile on
result = runParallelWorkload();
mpiprofile viewer
```

重点比较最忙与最闲 worker 的总时间。如果差异明显，可能是 `parfor` 迭代工作量不均、数据传输过多或分区方式不合理。`mpiprofile` 和 `parfor` 依赖 Parallel Computing Toolbox，诊断报告必须注明产品依赖。

## Agent 判断原则

1. 只有 CPU 高、任务有进展、内存和温度正常：倾向“正常计算负载”，不应报告崩溃或泄漏。
2. CPU 高且同一函数反复出现在 profile 调用历史、任务无进展：检查死循环、异常递归或收敛条件。
3. CPU 高并伴随温度持续上升：先控制硬件风险，再做代码热点优化。
4. CPU 高并伴随内存持续增长：转入内存/OOM Runbook。
5. 并行 worker 长时间等待或负载差异大：分析通信量和任务划分，不要简单增加 worker 数量。
