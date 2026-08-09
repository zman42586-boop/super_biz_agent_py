# 典型案例：MATLAB 偏好设置损坏导致无法启动

> 类型：MathWorks Support 案例归纳（非本项目真实事故）
> 标签：startup crash、preferences、prefdir、Windows

## 现象

MATLAB 双击后立即退出、卡在启动画面，且没有 License Manager Error。Windows 用户配置目录中的某个版本偏好设置可能已经损坏。

## 排查与处理

先备份崩溃日志和原偏好目录。关闭 MATLAB 后，将 `%APPDATA%\MathWorks\MATLAB\R20XXy` 重命名为带 `_old` 后缀，再启动 MATLAB 让其生成新目录。不要删除 `_licenses` 目录。恢复自定义设置时逐项迁移，避免把损坏文件整体复制回来。

## 来源

- https://www.mathworks.com/matlabcentral/answers/97167-why-will-matlab-not-start-up-properly-on-my-windows-based-system
