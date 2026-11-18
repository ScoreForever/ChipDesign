# EDA 后 FPGA 验证记录（2026-11-10）

**本地预验证通过；新版 Vivado 布线与上板尚未执行。** 由于当时的远程 Windows/Vivado 构建主机不可达，本轮未操作该工作区或开发板。

EDA 自动独立重跑和 Calibre 结果见 [EDA 报告](../eda_lab5_20261110/independent_calibre/README.md)。该报告仍有 ERC 人工评审及最终填充版多角时序等残项；FPGA 功能验证不能替代流片签核。ASIC 的 +0.444 ns 时序结果也不能作为 FPGA 下载依据。

## 输入与已完成检查

固定 `aa778875eed49ada6b7b36bf10b1c0cdce2ad3c9`（ABI 5.0、8×8），从 `git archive` 解包到独立目录。25 项 FPGA RTL／约束／脚本哈希与当前工程一致，另记录 filelist 哈希。最新 ASIC Wire Bond keepout 只影响物理流程，不进入 FPGA 构建。

| 检查 | 实际结果 |
|---|---|
| FPGA 存储模型完整程序回归 | 85 项 PASS，包括实际 JTAG 路径用例 |
| 非法描述符及生命周期 | 19 类检查通过，详见 summary 的 negative_tests |
| 单元及存储压力测试 | PASS；包括 20,000 组独立量化向量、MAC、双 SRAM、转发、随机停顿和排空 |
| 生产配置七模型重放 | 7 模型 × 2 次 PASS；波形及算子 Trace 均关闭 |
| 生产配置一致性 | 输出、全部中间张量写入检查通过；周期、写次数和每次全部 24 个性能计数与同源码 Trace 开启回归一致 |
| 真实 TAP 动态 CDC | 普通 100 组、突发 32 组 PASS；含相位／频率变化、背压、溢出、停 TCK、TMS／全局复位和恢复 |
| 新版 Vivado 50 MHz 布线及静态 CDC | NOT RUN：上板机连接受阻 |
| 下载、板上推理、板上 CDC／恢复 | NOT RUN |

七模型周期是 RTL 仿真结果，尚无本轮板测结果。连续两次的缓存状态可以导致少量周期差异。

| 模型 | 第一次周期 | 第二次周期 |
|---|---:|---:|
| ECG | 13,989 | 13,989 |
| Fashion CNN | 36,016 | 36,016 |
| GTSRB L | 79,756 | 79,756 |
| HAR | 15,826 | 15,821 |
| KWS L | 72,006 | 72,003 |
| KWS M | 73,716 | 73,713 |
| MNIST L | 30,836 | 30,836 |

## 连接恢复后的执行顺序

1. 检查目标 Windows/Vivado 主机、USB 及板卡占用。将快照放到全新隔离目录，保留其他工作区和正在使用的进程。
2. 设置 `CHIPDESIGN_TRACE=0`，用当前生产 RTL、8 列、50 MHz 做全新综合／布局布线／bitstream。下载前要求 setup WNS ≥ +0.344 ns、TNS = 0、hold WNS ≥ 0，并核对未约束路径和其他时序检查。
3. 对同一次布线运行 `verification/fpga/cdc/run_npu_jtag_cdc.py`：精确核验 72 请求位／37 响应位的 109 个端点、20 ns bundled-data 约束及同步器第二级 setup／hold；保留原始警告，不增加豁免。核对 RTL、约束、构建证据和 bitstream 哈希一致。
4. 确认板卡占用已结束后，重新确认目标器件和下载通道，下载合格 bitstream，记录下载日志、DONE／CRC 属性及实际 ABI／阵列能力。设备身份不能从历史配置推断。
5. FT232H／OpenOCD 以 TCK 1 MHz 做 smoke、全算子和非法输入测试、七模型各两次推理及性能计数对比，再做 CDC 停钟、复位、watchdog、恢复测试。
6. 归档本轮板测证据。实体按键复位、断电重启及电气检查只有实际完成并有记录后才可签核。

## 复现与证据

本地快照：`build/fpga_signoff/area129_after_eda_20261110/checkout`。源归档 `aa77887_source.tar` 的 SHA-256 为 `cdf2984ae47ebcfa054ca53f1df0504d8e9bb66c1f52ffa15962eb0c90081545`。归档保存在 build 下，不作为验证报告中的源码副本提交。

在快照根目录运行完整回归：

```sh
python3 hardware/npu/scripts/run_program_regression.py --output build/rtl_fpga --define CHIPDESIGN_FPGA_MEM --packaged-models --jobs 4
```

生产配置重放辅助脚本放在快照的父目录，运行 `python3 replay_production.py`。它复用完整回归的七模型命令和写入事件，只调整可选 Trace 接口的预期：容量为 0、未实现的 Trace 数据孔返回 BAD_ADDRESS；保留全部模型／输入／输出及中间写入检查，并比对全部性能计数。既有通用 runner 假定 Trace 开启，直接传入两个关闭宏会因容量预期 1024 而失败；该次失败日志仍在本地 `build/rtl_production`，没有将其计为设计错误或通过结果。

`build_frozen_windows.py` 放在相同父目录，与 `frozen_inputs.json`、`filelist_sha256.json` 及 `checkout/` 并列，在 Windows 运行即可调用既有构建器。它校验冻结输入、强制关闭 Trace，仅构建，不下载。Vivado 根目录通过 `--vivado-root` 显式传入，避免把本机安装路径写入报告。

- [公开结果摘要](evidence/public_summary.json)：保留回归计数、生产模型范围和未完成的 FPGA 门禁。
- [冻结输入](evidence/frozen_inputs.json)、[filelist 哈希](evidence/filelist_sha256.json)
- 原始逐用例日志、构建目录和远程主机路径不随公开仓库发布；完整证据留在本地/外部归档。

服务器环境未找到可用 Vivado，未安装新工具。数字 CDC 仿真不模拟亚稳态，不能替代当前布线的物理约束复核。

再次连接记录：2026-11-10T22:49:13+08:00，按用户要求重试，本机 SSH 仍超时（exit 255），经 `soc` 的 TCP 探测也超时（exit 1），尚未恢复连接。详见 [连接重试证据](evidence/connection_retries.json)。

连接重试 2026-11-10T22:58:07+08:00：本机 SSH 与经 `soc` 的 22 端口探测均再次超时，本机 ARP 仍未解析；没有执行新的 FPGA 构建或下载。
