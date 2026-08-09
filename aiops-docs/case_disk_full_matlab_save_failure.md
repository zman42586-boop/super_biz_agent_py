# 典型案例：磁盘空间或权限导致 MATLAB save 失败

> 类型：公开故障模式归纳（非本项目真实事故）
> 标签：disk full、save、permission denied、MAT-file、temporary file

## 现象

MATLAB 计算正常，但保存结果时报 permission denied、无法写文件或磁盘空间不足。大型 MAT 文件写入时还可能需要临时空间，最终文件大小不是唯一容量依据。

## 排查与处理

检查目标盘和临时目录剩余空间、目录权限、文件占用和路径长度；保存前记录预计数据量。不要在写入失败后立即删除原结果或覆盖唯一 checkpoint。并行 worker 保存失败时还要核对 worker 身份与 JobStorage 权限。

## 来源

- https://www.mathworks.com/help/parallel-computing/troubleshooting-and-debugging.html
- https://www.mathworks.com/help/matlab/large-files-and-big-data.html
