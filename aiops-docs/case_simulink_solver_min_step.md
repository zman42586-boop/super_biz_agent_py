# 典型案例：Simulink 最小步长或零交叉告警

> 类型：官方诊断项归纳（非本项目真实事故）
> 标签：Simulink、min step、zero crossing、solver、nonconvergence

## 现象

仿真速度突然下降、CPU 持续高，日志出现最小步长违规、连续零交叉或求解器无法收敛。问题通常与模型刚性、不连续信号或参数尺度有关。

## 排查与处理

保留首次告警时间和对应模型状态，检查 Solver Diagnostics 中的 Min step size violation、Consecutive zero-crossings violation 等设置。先定位触发模块和数值条件，再调整模型、容差或求解器；单纯提高最大步数可能只会延长卡顿。

## 来源

- https://www.mathworks.com/help/simulink/gui/diagnostics-pane-solver.html
