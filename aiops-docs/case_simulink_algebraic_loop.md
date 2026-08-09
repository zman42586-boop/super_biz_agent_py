# 典型案例：Simulink 代数环导致仿真慢或终止

> 类型：官方 Simulink 故障模式归纳（非本项目真实事故）
> 标签：Simulink、algebraic loop、solver、simulation slow

## 现象

模型编译时报告 algebraic loop，仿真每个时间步需要迭代求解，CPU 长期高且推进缓慢；求解失败时仿真终止。

## 排查与处理

使用 `Simulink.BlockDiagram.getAlgebraicLoops(model)` 定位环路，并把诊断级别设为 warning 或 error。判断是真实代数约束还是人工环路，再考虑重构方程、合理初值或引入符合模型语义的延迟。不能只因为 CPU 高就杀掉仿真。

## 来源

- https://www.mathworks.com/help/simulink/ug/identify-algebraic-loops-in-your-model.html
- https://www.mathworks.com/help/simulink/ug/remove-algebraic-loops.html
