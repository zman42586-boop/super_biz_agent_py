# 典型案例：直接读取超大 CSV 导致内存耗尽

> 类型：官方大数据工作流归纳（非本项目真实事故）
> 标签：CSV、datastore、tall、OOM、chunk

## 现象

一次性 `readtable` 或导入多个大 CSV 时，MATLAB 内存快速上升并出现 Out of Memory。原始文件大小小于峰值内存，因为表、字符串和临时转换会产生额外占用。

## 排查与处理

使用 `tabularTextDatastore` 或 `datastore` 分块读取，只选择必要变量；适用计算可在 datastore 上创建 tall array。避免在最后无条件 `gather` 全部结果，否则会重新把数据拉回内存。

## 来源

- https://www.mathworks.com/help/matlab/datastore.html
- https://www.mathworks.com/help/matlab/import_export/tall-arrays.html
