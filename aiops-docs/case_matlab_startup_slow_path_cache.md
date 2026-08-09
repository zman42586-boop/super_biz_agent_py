# 典型案例：MATLAB 启动长时间停在初始化

> 类型：公开资料归纳的典型案例（非本项目真实事故）
> 标签：startup、Initializing、path cache、startup.m、网络路径

## 现象

MATLAB 能启动但长时间停在 Initializing 或 Busy，CPU 不一定很高。常见证据包括启动计时中 `matlabpath` 耗时异常、Java class path 指向网络目录，或 `startup.m` 执行了慢操作。

## 排查与处理

先记录启动阶段和耗时，再检查 `startup.m`、`matlabrc.m`、`javaclasspath` 及 MATLAB 路径中的网络位置。确认路径缓存是否启用并刷新缓存。不要把“启动慢”直接判成 CPU 故障；杀毒软件、网络路径和许可证搜索也可能造成相同现象。

## 来源

- https://www.mathworks.com/help/matlab/matlab_env/resolve-startup-issues.html
