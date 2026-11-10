# 非对齐 gather 优化
- [x] 保存起点并复现4通道3×3的小型失败性能用例。
- [x] 实现按连续子集读取，接口及模型格式不变。
- [x] 边界专项与全RTL、七模型回归。
- [x] mywin隔离源码50MHz完整实现。
- [x] 同一路由CDC复核，通过后板测，归档结论。

## 最新方向

用户明确要求用RTL结构优化解决反复负余量。当前选用分片gather+INIT_B预计算8相位cursor_step，去掉gather_last实验。当前源在steps_core.sv，20个冻结输入steps_sources_sha256.json。81程序/15专项/8JTAG/18native与七模型production全部通过；50MHz步骤版在checkout_steps/build/gather_steps_retry_50mhz（stage chipdesign_nocpu_lerm0pfg）进行。不能把第一版0.000/+0.059或旧镜像+.344作为本轮步骤版签核。

并行forward为最新候选：slot并行匹配、按head分段选最新nibble，旧/新18432组合对照通过；mutation检出错误wrap优先级。当前source core=steps_core.sv，activation=parallel_activation_store.sv，run_program_regression集成新tb。forward_*.json全套通过且81项周期及24计数与steps一致。build checkout_forward/build/gather_forward_50mhz，stage chipdesign_nocpu_mcx2e1pc，SSH4198。步进表版最终+.014/+.039，只作为对照。

最终状态：采用checkout_forward/build/gather_forward_50mhz，首轮标准route通过（无负余量重试）；WNS+.031/WHS+.034，LUT18464/DSP12。132动态CDC+109端点复核通过，已上板14次模型及5恢复检查全过，全部24计数与生产RTL一致。GTSRB128283->95147；最终SHA535c93afe24ab10f02b424a4a8a9d6918e01d9c56165572fb84e6b82336d6350。报告README.md为最终结论；早期进行中描述由本结果取代。setup余量较旧镜像+.344小，已明确披露。
