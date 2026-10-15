# TinyCNN-8-Flat 融合核心（`tinycnn8_npu_fused`）

生产SoC默认使用的NPU核心。2026-10-14冲突解决保留了main的AXI/SRAM三拍读取、
FPGA约束/DRC脚本和Q31舍入简化；旧GAP配置的综合结果不能沿用到本核心。它和分层参考核心 `tinycnn8_npu_top` 端口相同，MMIO ABI 也相同，
`tinycnn8_npu_mmio_wrapper` 通过 `FUSED_CORE` 参数在两者之间切换（默认为 1）。分层核心继续保留，
用作逐层 bit-exact 参考，也用于 profiler/trace 实验。

## 1. 结构

```text
 host 端口（不变） ─► 1 KiB 激活存储（8 个字节 bank）──每拍 4 B──► 4×8 WS 阵列（双 bank 权重）
                                                                      │ INT32，每拍 1 个位置
 写回 INT8 ◄── requant_lane ×1（II=1，4 级）◄── 2×2 max（INT32）◄── 输出侧累加（tile16）
```

| 模块 | 作用 |
|---|---|
| `tinycnn8_npu_fused.sv` | 固定的三层调度（C1+P1 → C2+P2 → FC）、权重预取、激活存储、输出侧累加、池化、写回 |
| `fused_ws_array.sv` | 4×N 权重驻留阵列。每个 PE 有两组权重，bank 选择位随操作数一起流动；部分和输入恒为 0 |
| `requant_lane.sv` | 单 lane 流水 requant，算术与 `requant_unit`（bias=0、offset=0）逐位一致 |

### 调度

每层都按 **K-major tile** 执行：同一组权重（4 行 K）装载一次后，服务一个 tile 内的 16 个位置。
这 16 个位置正好是 4 个 2×2 池化窗口，按窗口顺序发射。

| 层 | K 分组 | tile 数 | 发射数 | 权重行装载 |
|---|---|---:|---:|---:|
| Conv1（ic=1） | 按卷积核行：3 个 tap + 1 个 0，3 组 | 20 | 960 | 240 |
| Conv2（ic=8） | 每像素 4 个连续通道，18 组 | 5 | 1,440 | 360 |
| FC（160→C） | 4 个连续特征，40 组 | 1（1 个位置） | 40 | 160 |

- **激活读：** Conv1 一组 3 字节、Conv2/FC 一组 4 字节，地址都是连续的。8 个字节 bank 保证任意地址起的 4 字节能在一拍内读出。
- **双 bank 权重：** 第 n 组权重写入 bank `n & 1`。只要上一组使用同一 bank 的事务全部退休，就可以开始装载，
  装载与当前组的计算重叠。Conv1/Conv2 几乎没有权重气泡。
- **输出侧累加：** 阵列顶部的部分和恒为 0，跨 K 组的累加在输出端完成（g=0 时加 bias）。
  发射不再等前一组的结果，FC 的 40 组也能流水执行。

### 先池化，后量化

`requant` 在 bias 折叠进累加器之后是单调不减的：左移饱和、Q31 高位乘（multiplier ≥ 0）、
舍入右移、钳位，每一步都单调。所以

```text
max(requant(a), requant(b), requant(c), requant(d)) == requant(max(a, b, c, d))
```

逐位成立。这样每个窗口只做一次量化，Conv1/Conv2 合计 800 个 lane 操作，一个 II=1 的 lane
在计算过程中就能全部消化掉。

- **前提：** multiplier ≥ 0。训练导出的 scale 天然满足。融合核下 wrapper 会拒绝负 multiplier 的
  卷积参数提交（错误码 5）。
- **软件证明：** `tools/test_tinycnn8_golden.py::PoolBeforeRequantTests`。其中也验证了负 multiplier
  会破坏这个等式。

### 存储布局（1 KiB）

| 区域 | 地址 | 大小 | 生命周期 |
|---|---|---:|---|
| 输入 | `0..319` | 320 B | 主机写入，Conv1 读取 |
| Pool1 | `320..959` | 640 B | Conv1 写入，Conv2 读取 |
| Pool2 | `0..159` | 160 B | Conv2 写入（覆盖输入），FC 读取 |

和分层核心一样，每次推理前主机都要重新装载 320 B 输入。

## 2. 实测（iverilog，4×8，50 MHz 时钟下的周期数）

| 指标 | 分层核心（main 默认） | 分层 + tile16 | **融合核心** |
|---|---:|---:|---:|
| 单次推理周期 | 59,357* | 33,610* | **2,805** |
| Conv1 + Pool1 | – | – | 1,000 |
| Conv2 + Pool2 | – | – | 1,480 |
| FC | – | – | 325 |
| 权重行装载 | 9,760 | 4,360 | **760** |
| 阵列发射 | 2,440 | 2,440 | 2,440 |
| 阵列利用率（69,760 MAC / 32 PE） | 3.7% | 6.5% | **77.7%** |

\* 当前 main 上的分层核心带有 `109513f` 加入的地址寄存级。`1395821` 时对应的数字是 52,594 / 31,168。

在 50 MHz 下，单次推理从 0.62 ms 降到 **56 µs**。融合核心的周期数与类别数、输入数据无关，
golden 用例中全部是 2,805。

## 3. 面积（RTL 声明量，未综合）

| 项目 | 分层核心 | 融合核心 |
|---|---:|---:|
| 激活存储 | 2 × 4,096 B | **1,024 B** |
| 权重存储 | 2,048 B | 2,048 B |
| psum/tile 缓冲 | 512 B + 40 B | 512 B + 128 B（窗口） |
| 阵列 psum 输入斜移寄存器 | 28 × 32 bit | **0** |
| PE 寄存器 | 32 × 48 bit | 32 × 57 bit（第二组权重 + bank 选择位） |
| Requant 32×32 乘法器 | 8 | **1** |
| maxpool2x2_engine + vector_unit | 有 | **无** |

合计数据存储约从 10.8 KB 降到 3.7 KB。LUT/FF/DSP 和频率需要 Vivado（EGo1 xc7a35t）综合
后才能确认。仓库已保留FPGA顶层与DRC检查脚本，但尚未取得本融合配置的综合结果。

## 4. 验证

```bash
python3 hardware/npu/scripts/run_tinycnn8_fused_test.py
```

- `tb_requant_lane`：2 万个随机和边界操作数，II=1，与 `requant_unit` 逐个比对。
- `tb_tinycnn8_npu_top`（`-DTINYCNN8_CORE=tinycnn8_npu_fused`）：4×8 和 4×4 阵列，1/4/6/8 类。
- `tb_tinycnn8_npu_mmio_wrapper`：`FUSED_CORE=1/0` 两种配置；VERSION、负 multiplier 拒绝、错误码。
- `tb_tinycnn8_fused_golden`：21 个 golden 用例（random/impulse/extreme，1–8 类），每个连续跑两次任务。
  逐字节比对 Pool1/Pool2，比对全部 8 个 logit，并检查 profiler 闭合。

分层核心的原有回归（`run_tinycnn8_regression.py`）保持不变，仍然全部通过。

## 5. 后续方向

2026-10-14新增验收：优化训练包在agi上完成100条真实语音融合/分层对拍，
400个raw logits、80,000个池化字节和280,600个采样边沿全部通过；
10项事件故障注入按预期被拒绝。参见
[真实语音与逐周期验证](../../docs/FUSED_SPEECH_VALIDATION.md)。
现有`run_tinycnn8_fused_test.py`也会执行独立事件检查。


1. Vivado 综合拿到 LUT/FF/DSP/WNS，确认面积收益，并决定 bank 读口的流水级。
2. 累加器收窄到 24 位，multiplier 收窄到 16 位（每个 lane 用 1 个 DSP48）。需要导出工具加位宽检查。
3. 层描述符接口：把 sequencer 里的固定参数开放给软件，配合汇编器和 golden 模型。
4. 输入每次 MMIO 写 4 B，并做输入双缓冲。现在每次推理要 320 次 MMIO 写，主机装载时间很可能已经和
   2,805 周期的计算相当（尚未在 SoC 上实测）。
