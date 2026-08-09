# 典型案例：并行池丢失 worker 连接

> 类型：官方故障模式归纳（非本项目真实事故）
> 标签：parpool、worker、lost connection、crash dump

## 现象

`parfor`、`parfeval` 或 `spmd` 运行中提示客户端丢失 worker 连接，并行池被关闭。可能原因包括 worker 崩溃、内存耗尽、网络或资源争用。

## 排查与处理

先到 `parcluster("Processes").JobStorageLocation` 和对应 Job 目录查找 worker crash dump。若没有 dump，再检查节点内存、通信和网络。MEX 只在 worker 路径崩溃时，可在客户端用最小输入单独复现。

## 来源

- https://www.mathworks.com/help/parallel-computing/resolve-error-client-lost-connection-to-worker.html
- https://www.mathworks.com/matlabcentral/answers/336076-how-do-i-troubleshoot-the-lost-connection-to-worker-x-parallel-error
