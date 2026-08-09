# 典型案例：加载大型 MAT 文件产生内存峰值

> 类型：官方大文件工作流归纳（非本项目真实事故）
> 标签：MAT-file、matfile、v7.3、partial loading、OOM

## 现象

`load` 一个大型 MAT 文件时内存瞬间升高，即使实际只需要其中少量变量或数组切片。

## 排查与处理

先用文件信息确认变量和尺寸。对于支持部分访问的 Version 7.3 MAT 文件，使用 `matfile` 按变量或索引读取需要的区域。处理流程应分块并及时写出中间结果；不要为了“方便”先加载全部数据再切片。

## 来源

- https://www.mathworks.com/help/matlab/large-files-and-big-data.html
- https://www.mathworks.com/help/releases/r2023a/pdf_doc/matlab/import_export.pdf
