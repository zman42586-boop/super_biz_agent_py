# 典型案例：工具箱许可证 checkout 失败

> 类型：官方文档案例归纳（非本项目真实事故）
> 标签：license checkout、License Manager Error、toolbox、feature

## 现象

MATLAB 主程序可运行，但调用某个工具箱时报 `License checkout failed`。`license('test', feature)` 返回 1 也不代表一定能成功 checkout，因为许可证席位可能已被占满。

## 排查与处理

使用 `[status,errmsg] = license('checkout', feature)` 保存完整错误；核对 feature 名称、许可证有效期、服务器连通性和席位占用。不要把许可证失败误判为 MATLAB 代码错误，也不要反复重试制造更多连接压力。

## 来源

- https://www.mathworks.com/help/matlab/ref/license.html
