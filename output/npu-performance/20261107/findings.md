# 周期回退诊断

小 CNN 在相同命令输入和 FPGA 存储配置下复现 1434 周期，相比去请求流水前 895 周期。单独去掉请求缓冲后为 1000 周期。运行命令和日志见 repro.log、no_request_pipeline.log。

Fashion-CNN 当前基线为 90872 周期。单独去掉请求缓冲后为 56214 周期，激活应答等待计数由 23449 降到 78。注册模型基地址后计数保持不变。增加模型应答寄存以前为 54718 周期，原始 38fdd73 为 42363 周期。

候选保持模型应答寄存、写 FIFO 注册额度、独立张量查询模块。FPGA 模型基地址在配置改变时寄存，暂停新事务一周期等待基地址有效。共享读请求直达存储控制器，避免每次额外停顿。对 rowpad、FC、通道对齐 ROWS 的组，一次生成各行偏移；不规则卷积仍保留串行路径。

远端隔离候选 D:/IWS/advanced_soc/ChipDesign/build/fpga_signoff/npu_perf_20261107/checkout，build/fastprep_50mhz。Vivado 暂存 C:/Users/Lenovo/AppData/Local/Temp/chipdesign_nocpu_3qzlyruf。构建会话 57103。

新增共享存储配置回归，同一边沿改变模型大小并提交读写，交替访问 8KiB/4KiB 两个保留区，核对内容独立保留。FPGA 寄存基地址、原生 SRAM 和宏功能模型均通过。临时故障注入去掉 base_ready 的配置匹配后，测试在 130ns 检出旧基地址（xxxxxxxx 与 12345678 不符）。故障注入文件已移除。

第三候选使用二周期连续组准备并寄存 controller.config_ok。目录 checkout_config/build/config_50mhz，暂存 chipdesign_nocpu_kn01ir8d，会话 8698。该配置检查在下一条命令 EXEC 前已经更新，不应增加模型周期。

## 最终验收完成（2026-11-07）

选定 checkout_bounds / bounds_margin_50mhz，当前工作区源码已恢复并与该镜像逐文件核对。50 MHz 路由 WNS +0.344 ns、WHS +0.058 ns，TNS/THS 0，LUT 18,961。实现阶段额外 setup uncertainty 0.350 ns，验收时恢复原始 20 ns 时钟及零 user uncertainty。132 组动态 CDC 和 109 个预期端点复核通过后，已将 SHA256 ef41bc0d170e4c20cfc7c8533434827fc22357d5c8a2b26627d8ebd7e2442590 镜像下载 EGo1。七模型各两次、共 14 次输出及周期均匹配生产 RTL；五项板级 CDC/恢复检查全部通过。先前待运行描述已被本结果取代。物理复位按键及电气波形仍待人工检查。完整结论见 README.md，源码增量见 this_round.patch。本轮未提交或推送。
