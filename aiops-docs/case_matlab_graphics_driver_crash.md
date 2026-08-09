# 典型案例：图形驱动导致 MATLAB 绘图或启动崩溃

> 类型：官方文档案例归纳（非本项目真实事故）
> 适用：MATLAB R2023b 图形栈
> 标签：OpenGL、graphics driver、startup crash、renderer

## 现象

打开 figure、复杂绘图或桌面组件时 MATLAB 退出，崩溃日志指向显卡驱动或 OpenGL。MATLAB 可能在后续会话自动降级到软件 OpenGL。

## 排查与处理

保留 crash dump、显卡型号和驱动版本，使用最小绘图脚本复现。R2023b 可比较硬件与软件 OpenGL 行为，更新到经过验证的驱动。不要在没有调用栈证据时把所有启动崩溃归因于 GPU。

## 来源

- https://www.mathworks.com/help/matlab/ref/opengl.html
