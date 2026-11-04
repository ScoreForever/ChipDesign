# 七个发布模型的 FPGA 补充验证

2026-11-04，远程模型提交 `78664e9c5da977288dcf721b470f1fc09aa30793`。
该提交及其前一份文档更新已合入 `codex/fpga-int4-v2-validation-20261104`，保留此前两处 RTL 修复和 CDC 门禁。

## 结论

**7 个模型、全部 112 帧发布 golden 样本已完成 FPGA 补测，224 次实板推理均逐字节匹配参考。**
137 项 RTL 程序回归及生产 trace-off 重放分别通过，每项执行冷/热两轮并核对所有 materialized tensor write。
112 组实板冷/热重复的全部 24 项性能计数器与相应 RTL 逐项相同。25 个首帧配置的 cycles、writes、performance、operator_trace 与远程发布的历史基准完全一致。

本次使用原已签核的 **50 MHz / 20 ns、4×8、trace-off** 镜像：

```text
SHA-256 8bdb83a721a06f6ce53aa84ce455f6c0da3353af582f7c56c4a18a248fdf2c2c
```

模型更新没有修改构建输入的 FPGA RTL、XDC 或实现脚本。重新逐个核对原 provenance 的所有源文件字节 SHA-256，全部一致，见 [comparison.json](comparison.json)。因此本次复用原布局布线结果和 bitstream，没有重新宣称另一份实现时序，也未进行 25 MHz 实现。最终 STA 仍是 **WNS 0.000 ns、WHS +0.035 ns**；setup 裕量限制没有因模型补测而消失。

已再次经配置 USB 下载指定镜像，DONE=1、CRC_ERROR=0，随后通过外部 FT232H 1 MHz TAP 读取 ABI 4.0、4×8 阵列、8 KiB 双 SRAM、depth=0/max_ops=0。下载记录见 [program](program/)。未做 FPGA 全配置 readback。

## 样本、包和参考的核验

- 模型目录：`software/zoo/pretrained/{ecg,fashion_cnn,gtsrb_l,har,kws_l,kws_m,mnist_l}`。
- 按发布 MANIFEST 核验 63 个 Git blob 的 SHA-256，全部一致。按 Git blob 核验文本是为了避免 Windows checkout CRLF 改写影响发布摘要；实际模型镜像另行编译逐字节核验。
- 每个模型的 c4/c8 两份镜像由当前编译器重编译，14 份均与发布 `model.bin` 完全相同。
- 逐个核验全部 112 帧的整数输出、output_hex，以及 RTL 补测脚本中的 input_hex；现有 `check_golden.py` 也独立执行通过。
- 实板直接加载发布 c8 包、写入发布 input_hex，逐字节比较 output_hex。每帧 RESET/重新上传模型后执行冷、热两轮，和 RTL 的 cache 生命周期一致。
- 参考输出来自同项目整数解释器，不是形式证明。未重新训练，也未改变权重、量化参数或输入样本。

## 实板结果

每模型 16 帧 × 2 轮，均通过。表中核心耗时 = active_cycles / 50 MHz，不包含上传、下载和主机 RPC。
模型分类错误与硬件错误分开统计：112 帧中 107 帧参考分类符合标签，5 帧硬件正确复现了参考本身的误分类。这是抽样结果，不是全测试集精度。

| 模型 | 模型字节 | 物理算子数 | 激活峰值字节 | 核心 cycles 范围 | 核心 μs 范围 | 抽样标签正确数 |
|---|---:|---:|---:|---:|---:|---:|
| ecg | 3908 | 13 | 1600 | 21286 | 425.72 | 16/16 |
| fashion_cnn | 8008 | 5 | 2744 | 72811–72812 | 1456.22–1456.24 | 15/16 |
| gtsrb_l | 7688 | 10 | 8192 | 122804 | 2456.08 | 15/16 |
| har | 4216 | 7 | 2624 | 25043–25048 | 500.86–500.96 | 16/16 |
| kws_l | 7224 | 9 | 6248 | 126411–126414 | 2528.22–2528.28 | 15/16 |
| kws_m | 7072 | 11 | 5248 | 110112–110115 | 2202.24–2202.30 | 15/16 |
| mnist_l | 4592 | 10 | 3528 | 49415–49416 | 988.30–988.32 | 15/16 |

全部 writes、MAC issues、cycles 和 24 项详细性能见 [comparison.json](comparison.json) 与 [board/report.json](board/report.json)。输入、预期和两轮实际输出逐帧保留在 `board/<model>/frameXX/`。

## RTL 与生产配置覆盖

137 项 = 112 个 4×8 全融合 golden 帧 + 7 个首帧 4×4 + 7 个首帧 IR12 JTAG 路径 + 11 个可容纳的首帧融合消融配置。
每项重复两轮；每轮检查最终张量、完整 materialized tensor write 事件、状态、周期、24 个全局性能计数器；trace-enabled 回归还核验 operator summary 的顺序、起止和事件计数。

同一批 137 项以 `SYNTHESIS CHIPDESIGN_FPGA_MEM NPU_ENABLE_WAVE_TRACE=0 NPU_ENABLE_OP_TRACE=0` 重新编译重放，确认所有 tensor write、最终读回、cycles/writes 和 24 项计数器不变；trace bank 的不可用读错误与 max_ops=0 也检查通过。这一组与实际板卡生产编译配置相同，避免仅靠 trace-enabled 仿真推断板测。

发布的 7 个全融合模型都可在 8 KiB 双 SRAM / 16 descriptor 限制内运行；另有 17 个消融配置按当前编译器容量检查拒绝，未记作 RTL PASS 或硬件故障：

| 模型 | 配置 | 拒绝原因 |
|---|---|---|
| ecg | none | too many physical operators |
| ecg | no_requant | too many physical operators |
| fashion_cnn | none | activation capacity exceeded: conv1 needs [392,50568), capacity 8192; consider fusion/larger build |
| fashion_cnn | no_requant | activation capacity exceeded: conv1 needs [392,50568), capacity 8192; consider fusion/larger build |
| fashion_cnn | no_maxpool | activation capacity exceeded: pool3 needs [6664,8232), capacity 8192; consider fusion/larger build |
| gtsrb_l | none | too many physical operators |
| gtsrb_l | no_requant | too many physical operators |
| gtsrb_l | no_maxpool | activation capacity exceeded: q2 needs [2048,14336), capacity 8192; consider fusion/larger build |
| har | none | activation capacity exceeded: conv1 needs [576,16960), capacity 8192; consider fusion/larger build |
| har | no_requant | activation capacity exceeded: conv1 needs [576,16960), capacity 8192; consider fusion/larger build |
| kws_l | none | too many physical operators |
| kws_l | no_requant | too many physical operators |
| kws_m | none | too many physical operators |
| kws_m | no_requant | too many physical operators |
| mnist_l | none | too many physical operators |
| mnist_l | no_requant | too many physical operators |
| mnist_l | no_maxpool | activation capacity exceeded: pool3 needs [6664,8232), capacity 8192; consider fusion/larger build |

`no_requant/no_relu/no_maxpool` 指关闭该种融合，不是移除对应数学算子；`none` 指全部融合关闭。未通过扩容或改模型绕过硬件限制。

## 证据和交叉检查

| 层级 | 证据 |
|---|---|
| 独立发布 golden 核验 | [控制台](consoles/int4_v2_zoo_golden_20261104.log) |
| 137 项正常 RTL | [rtl/summary.json](rtl/summary.json) |
| 137 项生产 trace-off RTL | [production/summary.json](production/summary.json) |
| 224 次实板执行 | [board/report.json](board/report.json) |
| 实板/RTL 24 计数器、历史 25 配置、实现源 SHA | [comparison.json](comparison.json) |
| 指定镜像下载与 TAP | [program](program/) |

较大的 commands.txt、writes.hex、OpenOCD log/commands.jsonl 无损 gzip 归档；解压后可读取原始字节。模型、golden 和权重已由远程提交合入本分支，不重复复制大份模型数据。父目录 `evidence_sha256.json` 覆盖本次全部新证据，提交后逐个核对 Git blob。

## 仍未覆盖

本次已消除“7 个模型导出不可访问”的阻塞，完成所有发布样本的模型级 FPGA 验证。完整数据集 `test_frames.pt` 未包含在发布包中，本轮未下载/重建全部公开数据集、未重评全测试集精度；不引用远程训练精度当作本机 FPGA 的新测量。

按此前安排，物理 RST/S6、断电恢复仍待现场操作；电气波形没有实测，Flash 自动启动没有测试。前轮的停 TCK、忙时协议 RESET、重复 START 和 watchdog 自动测试仍有效，见父报告；本补测没有把协议 RESET 写成物理复位。

## 复现

从本分支仓库根目录运行，output 必须使用新目录。板测要求先按原报告加载已签核镜像并启动 FT232H/OpenOCD，硬件脚本串行占用 TAP：

```powershell
python software/zoo/check_golden.py
python verification/fpga/int4_v2_zoo_rtl.py --output build/zoo_rtl_recheck --jobs 4
python verification/fpga/run_int4_v2_production_rtl.py --source-evidence build/zoo_rtl_recheck --output build/zoo_production_recheck --jobs 8
python verification/fpga/int4_v2_zoo_board.py --build build/int4_v2_50mhz_20261104_r2 --output build/zoo_board_recheck
python verification/fpga/check_int4_v2_zoo.py --rtl build/zoo_rtl_recheck --production build/zoo_production_recheck --board build/zoo_board_recheck --build build/int4_v2_50mhz_20261104_r2 --output build/zoo_comparison.json
```

板测脚本拒绝非本次已签核摘要的 bitstream。若未来重新实现，需要重新审查时序、CDC 和 bitstream 身份，不能只修改这个摘要就沿用当前结论。
