# 典型案例：MATLAB Java Heap Space 耗尽

> 类型：官方文档案例归纳（非本项目真实事故）
> 标签：Java heap、OutOfMemoryError、JVM、desktop

## 现象

错误包含 `java.lang.OutOfMemoryError: Java heap space`，但 MATLAB 工作区变量并不一定很大。大量 Java 对象、GUI、第三方 JAR 或未释放引用可能占满 Java 堆。

## 排查与处理

区分 Java 堆与 MATLAB 数值数组内存。可在设置中检查 Java Heap Size，调整后需重启。默认值通常足够；增大 Java 堆会减少 MATLAB 数组可用内存。如果增大后仍增长，应检查 Java 引用和泄漏，而不是持续提高上限。

## 来源

- https://www.mathworks.com/help/matlab/matlab_external/java-heap-memory-preferences.html
- https://www.mathworks.com/help/matlab/matlab_prog/resolving-out-of-memory-errors.html
