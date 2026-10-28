# 无 CPU 可编程 NPU SoC

默认源文件表：`sim/filelists/chipdesign_nocpu.f`，顶层 `my_soc_top`。
架构、协议、主机使用和验证入口统一见仓库根 README、docs/ARCHITECTURE.md、docs/CPU_LESS_CONTROL.md。
默认模型与激活容量均为8192 B，分别接独立同步存储。主机输入提交和任务完成均等待激活写队列排空。
