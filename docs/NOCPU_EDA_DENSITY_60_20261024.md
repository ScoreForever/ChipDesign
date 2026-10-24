# No-CPU 冻结基线：60% 利用率复查（2026-10-24）

按用户要求，单独复查 60% 目标利用率。输入仍为 14:45 冻结的 No-CPU 五算子 NPU，
快照 `f9ae8f1094eb8e2ead264954a7c38d29b3210a65eac95c1e2c19338d2ab94100`。
不重新综合，不引入最新版接口或性能计数器。

## 实验设置与状态

2026-10-24 16:33（新加坡时间）启动，16:46 完成 placement，16:59 进入布线，17:15 完成最终导出。
实际核心为 2380 × 2380 µm，5.664400 mm²，比原基线缩小 16.21%；
模块边界为 2459.52 × 2459.52 µm。这里仅确认 floorplan，尚不能判断详细布线是否收敛。

唯一实现参数变化为 floorplan 面积公式中的 `0.50 → 0.60`：

```tcl
set side [expr {ceil(sqrt($standard_area / 0.60 + $macro_area) / 20.0) * 20.0}]
```

沿用同一 Genus 映射网表、SDC、TT 库、20 ns 核心/1000 ns JTAG 时钟、宏位置、halo、
PDN、METAL2–6 信号布线和 CTS/hold/布线优化设置。局部优化密度上限仍为原来的 0.75，
不把它误称为全局目标利用率。

远端独立目录为基线根目录下的 `physical_u60/`；原 `physical/` 和归档结果保持原样。
`experiment.json`、`inputs.sha256` 和 `baseline_to_u60.patch` 保存输入指纹与精确脚本差异。
启动时已核验网表、SDC、基线脚本及全部 375 个冻结源文件。

## 对照基线

| 指标 | 原 50% 基线 | 60% 实验 |
| --- | ---: | --- |
| 核心面积 | 6.760416 mm² | 5.664400 mm²（−16.21%） |
| 50 MHz setup WNS | +0.821 ns | **+0.423 ns** |
| hold WNS | +0.003 ns | **+0.004 ns** |
| 静态功耗估算 | 374.686510 mW | **374.881217 mW** |
| 常规/电源 DRC | 通过 | **通过** |
| 信号/电源连通性 | 通过 | **通过** |
| 天线与布局合法性 | 通过 | **通过** |

以详细布线后的实际报告评价结果，不能根据 floorplan 缩小或早期时序直接宣称成功。
功耗对照使用同一 Innovus static 设置（input/seq activity 均为 0.1，无工作负载波形）。
本实验与原基线均为单 TT 角、课程 captable 的实现评估，不代表晶圆厂签核。

## 中间检查

- Placement 合法性通过，129,772 个已放置实例，未放置数 0。
- 工具 placement density 62.12%；按活动标准单元面积/非宏核心面积计算为 60.27%，
  两者分母不同。活动标准单元面积约 3.171785 mm²，单个 SRAM 几何面积 0.401444 mm²。
- 64×64 几何密度图最高格约 82.92%，图中排除 filler/tap/decap/antenna。
- preCTS setup WNS +0.313 ns，TNS 0，违规路径 0；尚不是布线后时序。
- Placement 全局资源估计 overflow 133（H 114，0.03%；V 19，四舍五入 0.00%）。
- 电源规划阶段 DRC 和连通性通过；信号详细布线检查待完成。
- CTS 核心时钟 skew 约 0.133 ns（目标 0.150 ns），postCTS setup WNS +0.270 ns。
- 初始 CTS hold WNS −0.064 ns、2 条违规路径；同流程修复后 hold WNS +0.002 ns、TNS 0、
  违规路径 0。以上仍是详细信号布线前数据。

## 最终 60% 结果

| 指标 | 60% 实测值 | 相对 50% 基线 |
| --- | ---: | ---: |
| 核心尺寸 | 2380 × 2380 µm | — |
| 核心面积 | **5.664400 mm²** | **−16.21%** |
| 模块边界面积 | 6.049239 mm² | −15.75% |
| 活动标准单元面积（排除 filler/tap/decap/antenna） | 3.186056 mm² | −0.02% |
| 活动标准单元数 / SRAM 数 | 107,345 / 1 | +9 / 0 |
| 活动标准单元 / 非宏核心面积 | 60.54% | 基线 50.11% |
| 工具 post-route 报告密度 | 62.404% | 基线 51.580% |
| 64×64 密度图最高格 | 84.91% | 基线 81.94% |
| post-route setup WNS / TNS / 违规路径 | **+0.423 ns / 0 / 0** | −0.398 ns WNS |
| post-route hold WNS / TNS / 违规路径 | **+0.004 ns / 0 / 0** | +0.001 ns WNS |
| max_cap / max_tran / max_fanout / max_length | 均 0 | 均 0 |
| Innovus 静态功耗（含时钟树和 post-route RC） | **374.881217 mW** | +0.052% |
| 常规信号/时钟路径中心线总长 | **7.125082 m** | −1.16% |
| 最终全局拥塞 overflow | 124（H 103，0.03%；V 20，0.01%） | 基线 111 |

最终 placement、常规/电源 DRC、信号/电源 connectivity 和 antenna 均通过，未放置实例为 0。
拥塞报告原文总数为 124，而列出的 H 103 + V 20 为 123；这里保留工具原值，未自行修正，
该全局资源估计与最终详细布线 DRC 属于不同指标。
GDS、DEF、SPEF、SDF、LEF、SDC 和 post-route 网表均已导出；网表核验仍为单个
`i_control_i_model_store_i_sram` SRAM 和 7 个 No-CPU 顶层端口。

60% 结果在本次单 TT 评估标准下通过：面积显著下降，setup 裕量减少到 423 ps，hold
裕量只有 4 ps。它适合作为当前实现候选，但不能据此宣称多 PVT 角或 foundry signoff
已收敛。日志保留 5,558 条 warning 和 2 条 IMPSP-9099（未定义扫描链）诊断，和原基线
相同类型；本轮没有伪造 DFT 扫描链。

本地成果：

- `docs/validation/nocpu_density_u60_physical_20261024.json`：60% 完整物理 PPA、检查和假设。
- `docs/validation/nocpu_density_u60_comparison_20261024.json`：与 50% 基线的机器可读比较。
- `work/xlab-nocpu-eda-20261024/density_u60_evidence/analysis/final_layout.png`：60% 最终密度图。
- `work/xlab-nocpu-eda-20261024/density_u60_evidence/analysis/routed_wires.png`：60% 实际 DEF 常规布线图。
- `work/xlab-nocpu-eda-20261024/nocpu_u60_ppa_layout_evidence.tar.gz`：完整证据包，附 `u60_evidence_manifest.json` 文件哈希清单；本地整体与逐文件校验结果见 `work/xlab-nocpu-eda-20261024/u60_evidence_integrity.json`。
