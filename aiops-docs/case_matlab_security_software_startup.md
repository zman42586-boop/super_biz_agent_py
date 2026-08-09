# 典型案例：安全软件干扰 MATLAB 启动

> 类型：MathWorks Support 案例归纳（非本项目真实事故）
> 标签：antivirus、EDR、startup、CPU、file access

## 现象

MATLAB 启动明显变慢或偶发失败，同时杀毒/EDR 进程 CPU、磁盘活动升高。安装目录、JAR、工具箱缓存或临时文件被逐个扫描。

## 排查与处理

对比启用和停用安全策略时的启动计时只能在 IT 管理下进行，不应直接关闭安全软件。收集 MATLAB 启动阶段、EDR 日志和被扫描路径，由 IT 按最小范围评估白名单。还应排除网络 JAR、路径缓存和损坏偏好设置。

## 来源

- https://www.mathworks.com/help/matlab/matlab_env/resolve-startup-issues.html
- https://www.mathworks.com/matlabcentral/answers/97167-why-will-matlab-not-start-up-properly-on-my-windows-based-system
