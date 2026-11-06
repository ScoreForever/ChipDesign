# 无 CPU 可编程 NPU SoC

默认源文件表：`sim/filelists/chipdesign_nocpu.f`，顶层 `my_soc_top`。
架构、协议、主机使用和验证入口统一见仓库根 README、docs/ARCHITECTURE.md、docs/CPU_LESS_CONTROL.md。
ABI 5.0 使用两颗 8 KiB SRAM。模型放在第二颗 SRAM 的高地址端，激活张量共享两颗 SRAM 的剩余空间。主机输入提交和任务完成均等待激活写队列排空。
