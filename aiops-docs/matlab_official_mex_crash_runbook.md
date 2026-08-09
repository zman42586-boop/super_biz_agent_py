# MATLAB MEX 加载失败与进程崩溃官方 Runbook

> 适用版本：MATLAB R2023b 及兼容版本
> 检索标签：MATLAB、MEX、mexw64、Invalid MEX-file、DLL、运行库、版本兼容、access violation、crash dump、java.log

## 官方来源

- [Run MEX File You Receive from Someone Else](https://www.mathworks.com/help/matlab/matlab_external/before-you-run-a-mex-file.html)
- [Invalid MEX File Errors](https://www.mathworks.com/help/matlab/matlab_external/invalid-mex-file-error.html)
- [Troubleshooting MEX API Incompatibilities](https://www.mathworks.com/help/matlab/matlab_external/troubleshooting-mex-api-incompatibilities.html)
- [MathWorks Support：定位 MATLAB crash dump](https://www.mathworks.com/matlabcentral/answers/100816-how-do-i-locate-the-crash-dump-files-generated-by-matlab)

## 先区分“加载失败”和“运行时崩溃”

### Invalid MEX-file

常见原因包括：

- 缺少 MEX 依赖的 DLL 或专用运行库；
- MATLAB 版本、操作系统平台或 MEX API 不兼容；
- 缺少生成该 MEX 文件所使用的编译器运行库；
- 依赖库不在系统路径或 MEX 文件所在目录。

这类错误通常在 MEX 加载阶段出现，应先检查依赖和兼容性，不要直接归因于内存泄漏或指针越界。

### MATLAB 进程崩溃

如果 MEX 已成功加载，但调用后 MATLAB 进程退出，应保留：

- `matlab_crash_dump.<pid>`；
- `java.log.<pid>`；
- `hs_error_pid<pid>.log`；
- MATLAB 版本、MEX 文件哈希、构建编译器和运行库版本；
- 调用参数、最小复现步骤、系统 CPU/内存快照。

MathWorks Support 说明，MATLAB 崩溃时可能生成上述一种或多种日志，文件名中的数字对应进程 ID。实际路径可能随版本和操作系统变化，应以 MATLAB 崩溃提示及 MathWorks 支持文档为准。

## 排查步骤

1. 用最小输入单独调用 MEX，确认是否稳定复现。
2. 检查 MEX 平台和 MATLAB 版本兼容性。
3. 检查 DLL、编译器运行库和专用第三方库是否齐全。
4. 如果有源码，使用当前 MATLAB 支持的编译器重新构建，并保持相关对象文件使用一致的 MEX API 选项。
5. 对比禁用 MEX 的纯 MATLAB 路径；只有 MEX 路径崩溃时，才能提高对 MEX 的怀疑等级。
6. 从 crash dump 中提取异常类型和调用栈，但不要在没有栈证据时断言 NULL 指针、野指针或越界访问。

## Agent 输出要求

- 明确标注事实、推断和待验证项。
- `Invalid MEX-file` 应优先给出依赖与兼容性检查。
- `access violation` 且调用栈指向 `.mexw64` 时，可以建议检查指针、数组边界及 MATLAB C Matrix API 使用，但必须说明这是基于调用栈的推断。
- 没有 crash dump 时，结论应保持为“原因未确定”，并建议检查操作系统事件日志、MATLAB 日志和用户是否手动结束进程。
