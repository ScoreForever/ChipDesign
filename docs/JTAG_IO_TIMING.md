# FT232H ↔ EGo1 JTAG 外部时序模型

适用范围：FT232H 3.3V，TCK/TDI/TMS/TDO与地直接连接，用户确认杜邦线不长于30cm，无电平转换/隔离/缓冲器；名义TCK最高1MHz。当前TCK接J5第21脚/H16。
这是一份有边界的STA接口契约。传播预算和TMS输出包络是工程假设，尚未用示波器逐项测量；通过本模型不代表电气波形、温压或任意线缆已经认证。

## 器件与边沿依据

- [FTDI官方FT232H datasheet v2.2](https://www.ftdichip.cn/Support/Documents/DataSheets/ICs/DS_FT232H.pdf)，第33页，图4.14和表4.4：MPSSE输出clock-to-out为0..7.5ns；输入端采用11ns setup、0ns hold预算。表中t5/t6文字均写TDI/DO，但图在TDO/DI处标出接收窗口，按图解释并保守建模。原图为30MHz，可分频到较低速率。
- 本机OpenOCD提交`10b7a647da03cb64fd25db76261712c388ad6468`，`src/jtag/drivers/ftdi.c`：`JTAG_MODE=LSB_FIRST|POS_EDGE_IN|NEG_EDGE_OUT`。配置显式固定`ftdi tdo_sample_edge rising`。
- 本地TAP在TCK上升沿接收TDI/TMS，TDO通过反相TCK驱动的寄存器在下降沿更新。
- TMS同样按下降沿MPSSE输出建模。表4.4未单列TMS的数值保证，使用与DO相同的输出包络属于接口假设，不能误称厂商独立保证；20ns额外guard也覆盖此建模保留量，仍需物理测量才能消除该假设。

## 预算与公式

STA参考时钟在FPGA的`tck_i`端口。设FTDI→FPGA时钟传播为C、数据传播为D、FPGA→FTDI数据传播为R。
本配置规定`C,D,R ∈ [0,5]ns`。30cm导线在0.5c传播速度下约2ns，另保留3ns给板上走线/接头/模型余量；此5ns是短线设计包络，不是测量结果或器件数据。
另取`G=20ns`外部边沿guard，为1MHz周期的2%，覆盖外部时钟边沿及未精确量化的建模余量；不将其作为内部同边沿寄存器的hold uncertainty。

| 约束 | 计算 | 写入XDC |
|---|---|---:|
| 输入最早变化（相对下降沿） | tCOmin+Dmin-Cmax-G | -25ns |
| 输入最晚稳定（相对下降沿） | tCOmax+Dmax-Cmin+G | +32.5ns |
| 输出setup预算（相对上升沿） | FTDI_setup+Cmax+Rmax+G | +41ns |
| 输出hold预算 | Cmin+Rmin-FTDI_hold-G | -20ns |

同时设置min/max，输入使用`-clock_fall`，输出参考默认上升沿。不能把输入错误地当成上升沿发射，也不能只写max而漏hold。
由于TAP存在反相TCK，Vivado TIMING-18会检查两种参考边沿。按[AMD规则说明](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Description?contentId=rzcj_bvvmcdV6BQwp4aPAw)，额外表达同一窗口：输入相对上升沿的min/max为475/532.5ns；输出相对下降沿的min/max为480/541ns，均用`-add_delay`保留原表达。这是半周期坐标转换，没有增加物理数据事件或放宽约束。例如输入`500+32.5=0+532.5`，输出setup要求`1000-41=1500-541=959ns`，hold要求`0-(-20)=500-480=20ns`。在同一旧路由网上试验，两种表达的setup/hold slack完全相同，补全表达后TIMING-18归零；没有使用waiver。
如果线缆、适配器、采样边沿、接口电压或TCK上限改变，必须重建模型和验证；不得沿用本结果。

## 非数据接口

- `rst_ni`是异步物理按键。仅切断它到顶层复位同步器CLR端点的路径；不切断同步释放后到功能寄存器的recovery/removal路径，也不把整个复位网或时钟域整体false-path。
- 三个LED为人眼状态指示，没有同步接收时钟，仅对输出端点设false-path；内部计数器及状态寄存器仍检查时序。
- DMI内部跨域仍使用原六组max_delay约束，与外部JTAG数据端口时序分别检查。

验收应核查实际最大/最小路径、IO_DELAY、时钟边沿、SLACK及端点数量，确认路径未被切断，并检查TIMING-18是否消失。之后使用新bit执行GDB、43组板上回归、SRAM/复位恢复及不同TCK频率的通信测试。以上只签核声明的接口模型；真实信号完整性和各外部假设的测量确认仍应单列。

## 本次实现与板上结果

- 全新实现：`build/fpga_jtag_io_20261020_r2/`。CPU/NPU计算RTL未修改；TIMING-18为0，无关键方法学告警或关键CDC告警。
- 系统50MHz：WNS +0.913ns、WHS +0.013ns。TDI/TMS/TDO六组setup/hold路径均有效，无数据路径例外；外部最小setup裕量+448.044ns、hold裕量+472.005ns。
- bit SHA256：`d45dc5ee146f71a7247886d1eba462391ea139ba0de283db4d85122af848e368`。已经1234-tulA下载线烧写，DONE=1、CRC_ERROR=0。
- 正式验证目录：`build/fpga_validation_jtag_io_20261020/`。GDB、43组/190次推理、26,365项固件检查、684次IRQ检查、6种全SRAM模式和6轮复位恢复（额外12次推理）通过。
- 10/100/500/1000kHz每档8轮、每轮16个随机SRAM字及BootROM比较全部通过，scratch恢复，CPU最终暂停。
- 标准Rtl入口中的两套既有回归（NPU33,936次逐元素比较）、11项证据检查、停TCK复位专项均通过。
- 汇总为`status=PASS, functional_status=PASS, physical_status=PASS_WITH_ASSUMPTIONS`，只适用于本文声明的包络。不能将本结果写成无条件电气/温压签核，亦未关闭ISR、忙时复位和覆盖率等其他测试缺口。

[可读汇总](../verification/fpga/results/2026-10-20-jtag-io/report.md)与[机器汇总](../verification/fpga/results/2026-10-20-jtag-io/report.json)保留哈希和范围；原始数据、构建产物及首次失败探查记录留在本地build目录。此前schema1/2报告不回填升级。

## 无CPU NPU TAP的CDC构建修复（2026-10-26）

以上DMI及2026-10-20板级结论属于原CPU流程。当前无CPU设计使用 `u_soc/i_host` 专用TAP，
不能套用 `u_soc/i_dmi_jtag/...` 的旧端点选择。合并后的FPGA入口曾存在此错配，本轮改为
`hardware/fpga/scripts/constrain_npu_jtag_cdc.tcl`，综合后施加约束并审计，布局布线后再次审计。
TDI/TMS/TDO的FT232H外部min/max及边沿预算保持原值，不用内部CDC例外放宽外部接口。

| 路径 | 位数 | 约束方式 |
|---|---:|---|
| req_payload → cmd_op_o/cmd_addr_o/cmd_data_o | 72 | `max_delay -datapath_only min(core period,TCK period)` |
| rsp_mailbox_data/error → reply_data/error | 37 | 同上 |
| req_toggle → req_sync[0]/D | 1 | 仅异步首级D端点false-path |
| ack_toggle → ack_sync[0]/D | 1 | 仅异步首级D端点false-path |
| 顶层及TAP的core/TCK复位同步器 | 4组、各2个CLR | 仅同步器异步断言端点false-path |

请求payload在请求完成前保持稳定，响应mailbox在下一响应前保持稳定；两个接收域均通过两级toggle同步
后才采样payload。min周期的飞行时间约束使109位bundled payload在这段等待期间到达。
源端使用实际源寄存器作为合法时序起点，目标为精确D引脚，避免从Q引脚强制path segmentation。
不设置全局异步clock group或payload false-path；若payload被其他例外切断，端点审计必须失败。

审计检查109个payload目标路径确实存在且采用该预算；检查req/ack两级同步器的ASYNC_REG与级间直接连线，
首级到次级的setup/hold路径继续正常计时；复位同步器后的recovery/removal路径也继续计时。
窄范围的CDC控制/异步复位例外不等同于对全部功能路径签核。旧bitstream和旧板级结果不能复用为本轮物理证据。

局部诊断已在 `build/dual_sram_vivado/synth.dcp` 上单独施加新约束并审计，产物为
`build/dual_sram_cdc_checkpoint_check/`。72条请求、37条响应均保留20 ns最大飞行时间路径，
最小余量分别18.964/18.975 ns；同步器次级setup/hold均存在，四组复位同步器各有两个CLR端点例外。
六组外部JTAG setup/hold路径保留原预算且无数据路径例外。
局部WNS仍为−0.778 ns，WHS由未约束异步首级路径造成的−4.775 ns恢复为+0.045 ns；
CDC报告有109项CE控制bundled-data警告、无Critical CDC，该分类依赖上述握手保持协议。
此诊断没有重新综合或布局布线，不能当作最终源码一致的物理结果；最终仍需使用新脚本重新构建。
`audit_summary.json`记录此范围、脚本指纹、同步器属性/连线断言和各组实际路径数。
