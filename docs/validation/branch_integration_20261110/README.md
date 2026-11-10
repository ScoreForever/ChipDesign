# 2026-11-10 main 分支整合与功能验证

整合起点为 `81876b3`。五个分支的冻结 SHA 见 [branches.json](branches.json)，保留原提交作者和合并历史：

- `power/r1-array-clock-enable-20261109` 包含 A2 权重流水、A 双宏模型读旁路及性能／验证工具。
- `perf/a2-model-pipeline-20261109` 和 `perf/a-dual-macro-20261109` 已随 R1 成为 main 的祖先，不重复应用。
- `perf/bottleneck-20261109` 的统计修正已以 `91dce1c` 原样拣选。合并冲突保留后续 `regression_counts()`、三种存储配置和覆盖率导出。
- `docs/abi5-consistency-20261109` 修正文档，并按整合后真实 SRAM 旁路／权重流水／寄存器使能结构补齐说明。

## 验证范围

ABI 5.0、8×8 阵列保持。以下均为数字 RTL 功能仿真或验证工具测试，不是物理签核。

| 检查 | 结果与原始证据 |
|---|---|
| 行为存储程序回归 | 85 项（含 4 项 JTAG）+19 组负向／生命周期，通过；[摘要](behavioral_summary.json) |
| FPGA 存储配置 | 81 项+19 组负向／生命周期，通过；[摘要](fpga_mem_summary.json) |
| RA1SHD 宏接口模型 | 81 项+19 组负向／生命周期，通过；[摘要](ra1shd_summary.json) |
| 块级与共享存储 | 三配置均通过量化向量、随机背压、写合并、转发优先级及六组共享 SRAM 压力测试；见相应回归日志 |
| Gather 专项 | 15 项，通过；[摘要](gather_summary.json) |
| 随机模型 | 80 项，通过；另外 24 项被编译器／规模上限拒绝，原因逐项保留，未计入仿真通过；[摘要](fuzz_summary.json) |
| 实际 TAP／突发 CDC | 100+32 项，通过；[普通 TAP](tb_npu_jtag_cdc.log)、[突发](tb_npu_jtag_burst_cdc.log) |
| CDC 极限扫描 | 150 个工作点功能通过，含 707 次复位、7604 次离散延迟注入事件、890 次 overrun 拒绝；[摘要](cdc_limits_summary.json) |
| 工具单元测试 | 14 项，通过；[日志](tooling.log) |
| 故障注入 | 见 [逐项结果](mutation_summary.json) 和 [日志](mutation.log)；预期 survivor 不计作检出 |

每个程序检查两次运行的每一笔物化张量写入，并核对最终输出。覆盖率为 [coverage.csv](coverage.csv) 中的功能计数，不能当作代码覆盖率或形式证明。

CDC 极限扫描中的 125/200 MHz 系统点及最快 TCK 点可出现低于 20 ns 的协议预算，属于超出当前板卡工作点的边界刻画，不能据“功能通过”宣称这些频率已物理安全。50 MHz 系统点的数字请求窗口为 40.01 ns；仍需在对应布线数据库中复核实际 bundled-data 最大延迟、同步器级间时序及复位。离散延迟注入不模拟模拟电路的亚稳态或 MTBF。

故障注入最终为 **20/24 检出**，严格门禁通过，没有非预期 survivor。`m07/r04` 为 180 秒有界超时检出；`m08/m13/c04` 在已说明的等价条件下存活；`c02` 缩短同步链仍通过数字仿真，必须由静态 CDC／物理复核检出，不能当作验证闭合。完整原始结论保留，未把这些 survivor 计作成功检出。

## 推理周期对比

同一批发布模型的第一个 golden frame，在 FPGA 存储配置 RTL 下均未回退。详见 [model_cycles.csv](model_cycles.csv)。

| 模型 | 81876b3 已上板周期 | 整合后 FPGA 配置 RTL 周期 | 周期减少 |
|---|---:|---:|---:|
| ECG | 15,854 | 13,989 | 11.76% |
| Fashion CNN | 47,176 | 36,016 | 23.66% |
| GTSRB L | 95,147 | 79,756 | 16.18% |
| HAR | 18,836 | 15,826 | 15.98% |
| KWS L | 86,536 | 72,006 | 16.79% |
| KWS M | 88,754 | 73,716 | 16.94% |
| MNIST L | 37,059 | 30,836 | 16.79% |

这些新周期来自普通回归的 trace 配置，不是新镜像实板测量，也不保证综合／布局布线后最高频率提高。功耗方面只增加了波前寄存器使能和活动率计数，尚无门控插入、SAIF 后功耗或实测功耗结论。

## 整合修正

1. 共享存储压力测试台把 `mdone/adone/wdone` 声明移到使用点前，解决本地 Icarus elaboration 失败。
2. 故障注入增加 `--unit-after-program`，先执行带协议断言的程序，再运行块测试；默认程序回归顺序保持不变。未注入故障的同一 30 程序子集及块测试先验证通过。
3. 故障注入超时清理整个子进程组，避免只杀 Python 父进程后遗留 `vvp` 或 shell 管道。对应两项单元测试覆盖超时和正常结束。
4. 验证 workflow 增加 main 的 push 触发；原本仅对 perf/power/verify 分支自动触发。

第一次故障注入实验在 m02 的独立 stalls 测试台长等待，被人工终止，不作为检出证据。最终证据来自改进后的新目录 `build/mutants/integration_20261110_v2`。超时检出会明确保留为 timeout，不冒充输出错误或断言检出。

## 复现入口

```bash
python3 -m unittest discover -s hardware/npu/scripts -p 'test_*.py' -v
python3 hardware/npu/scripts/run_program_regression.py --jobs 3 --packaged-models --output build/integration/behavioral
python3 hardware/npu/scripts/run_program_regression.py --jobs 3 --quick --packaged-models --define CHIPDESIGN_FPGA_MEM --output build/integration/fpga_mem
python3 hardware/npu/scripts/run_program_regression.py --jobs 3 --quick --packaged-models --macro-model hardware/npu/tb/ra1shd_functional_model.sv --output build/integration/ra1shd
python3 hardware/npu/scripts/run_gather_regression.py --output build/integration/gather
python3 hardware/npu/scripts/profile_fuzz.py --count 80 --seed 20261109 --memory fpga_mem --jobs 3 --output build/integration/fuzz
python3 verification/fpga/cdc/run_cdc_limits.py --jobs 4 --output build/integration/cdc_limits
# 先确认未变异设计回归和 CDC 通过，再执行故障注入
python3 hardware/npu/scripts/mutation_test.py --run integration --jobs 3 --timeout 180 --strict
```

## 物理验证状态

[2026-11-09 的 +0.533 ns 和上板记录](../../../output/npu-timing/20261109/README.md)仍只适用于冻结提交 `81876b3`。本次整合改变了生产 RTL，未复用旧 bitstream、未重新烧板；新 FPGA 布线时序／静态 CDC／实板，以及新 ASIC 综合、PnR、SDF 和 Lab 5 最终 GDS 检查需各自使用同源新交付物。

用户追加授权后，已在 `ssh soc` 的独立工程中启动本次整合 RTL 的 ASIC 综合并准备 Lab 5 环境。该工作另行记录，启动工具或找到许可证不计作签核通过。主机 RPC 优化、历史 CDC 报告的本地修改和 PPT 均未纳入本次分支整合。
