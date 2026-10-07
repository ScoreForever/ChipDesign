#!/usr/bin/env python3
"""Generate bounded presentation evidence from the accepted tiled experiment."""
import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result, output = args.results.resolve(), args.output.resolve()
    summary = json.loads((result / "summary.json").read_text())
    if summary.get("status") != "PASS" or summary.get("unit_regressions_skipped"):
        raise SystemExit("report requires full accepted regression, not a smoke run")
    output.mkdir(parents=True, exist_ok=True)
    case = summary["cases"][0]
    runs = case["runs"]
    names = ("baseline", "overlap", "tile7", "tile8", "tile16", "tile32")
    rows = []
    for name in names:
        run = runs[name]
        p, hw = run["perf"], run["hardware_profile"]
        tile = run["parameters"].get("SPATIAL_TILE", 16)
        tiled = name.startswith("tile")
        rows.append({"mode": name, "tile": tile if tiled else 0,
                     "network_cycles": p["TOTAL"]["cycles"], "conv2_cycles": p["2"]["cycles"],
                     "hardware_conv2_cycles": hw["c2"], "conv2_weight_rows": p["2"]["weight_rows"],
                     "conv2_weight_bytes": p["2"]["weight_bytes"], "matrix_inputs": p["2"]["matrix_inputs"],
                     "psum_bytes": tile * 32 if tiled else 32,
                     "tag_bytes": 64 if tiled else 0, "pack_bytes": 8 if tiled else 4,
                     "major_tile_storage_bytes": tile * 32 + 72 if tiled else 0,
                     "peak_inflight": hw["peak_inflight"]})
    with (output / "performance_summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    accepted = {"status": "PASS", "scope": summary["scope"], "git_head": summary["git_head"],
                "tools": summary["tools"], "compared_elements": summary["compared_elements"],
                "cases": [{"name": x["case"], "classes": x["classes"], "pattern": x["pattern"],
                           "modes": list(x["runs"])} for x in summary["cases"]],
                "regressions": [{k:v for k,v in item.items() if k != "seconds"} for item in summary["regressions"]],
                "source_sha256": summary["source_sha256"], "performance": rows}
    (output / "accepted_summary.json").write_text(json.dumps(accepted, indent=2, ensure_ascii=False)+"\n")
    base, chosen = rows[0], next(x for x in rows if x["mode"] == "tile16")
    b, o = base["network_cycles"], chosen["network_cycles"]
    cb, co = base["conv2_cycles"], chosen["conv2_cycles"]
    reduction, speed, c2speed = 100*(1-o/b), b/o, cb/co
    table = "| 调度 | 整网周期 | Conv2 周期 | C2 权重行 | 部分和容量 | tag/pack |\n|---|---:|---:|---:|---:|---:|\n"
    for row in rows:
        table += f"| {row['mode']} | {row['network_cycles']:,} | {row['conv2_cycles']:,} | {row['conv2_weight_rows']:,} | {row['psum_bytes']} B | {row['tag_bytes']}/{row['pack_bytes']} B |\n"
    regressions = len(summary["regressions"])
    mmio = [r for r in summary["regressions"] if r["test"] == "mmio_fileio"]
    report = f"""# TinyCNN-8：空间复用、trade-off 与综合前完成度

## 核心结论

相同4×8阵列、同一固定网络、相同合成参数及行为存储契约下，tile16 的整网周期
**{b:,} → {o:,}**，减少 **{reduction:.2f}%**，速度比 **{speed:.3f}×**；Conv2
**{cb:,} → {co:,}**，速度比 **{c2speed:.3f}×**。C2 Matrix输入仍为1,440笔，
权重行由5,760降至360，减少93.75%。主要 tiled 数据存储为584B，不等于综合面积。

完整实验有 **{len(summary['cases'])} 个网络用例、{summary['compared_elements']:,} 次逐层元素比较**，
另有 **{regressions} 项记录的单元/配置/oracle/MMIO回归**；正式 MMIO 覆盖
{len(mmio)} 个模式/用例组合、每组合3次连续任务。日志是有限测试证据，不是形式证明。

**范围：synthetic 参数和输入、独立NPU及生产MMIO后端、组合行为存储。未完成真实KWS
准确率、CPU/AXI整SoC仿真、综合、STA、频率、面积或功耗验证。**

## 1. 基础与增量

基于主线 `46d759b`：团队已有Matrix/Vector、TinyCNN/Q31以及正式SoC wrapper。
本次没有重写这些基础，也没有复制第二套Matrix核。

新增工作：三路可切换调度、K-major空间复用、多在途事务回收、独立整数golden、
事件trace、参数扫、生命周期验证、软件可读profiler与可复现回归/CI。

旧阶段11.12%结果是 gather/load overlap 的独立贡献；它不减少权重装载，不能与
本次 tiled 的节约量相加。新实验用最新主线与同一观察器重新比较。

## 2. 为什么优化调度，不先加PE

两层卷积都是8个输出通道，匹配现有8列。Conv2约占名义MAC的三分之二。
原调度每个空间位置、每个K组都重载四行权重，并等待一笔Matrix结果。
第一阶段重叠数据准备后，仍有约64%的C2周期在等结果。

新循环顺序：空间tile → 18个K组 → tile内位置。同一权重组装载一次，服务多个
位置；连续收集/发射期间也接收返回结果。按tag把INT32部分和写回各位置。
本组全部退休、Matrix idle后才能换权重；最后一组完成后串行量化/写回。

当前activation口每拍1byte，实施版每个pack收集4拍、发射1拍；两个pack交替使用，
并未把收集和发射彻底重叠成理想II=4，更不是II=1。多个计算延迟能重叠，但组间
仍需排空。权重复用不自动减少激活读取量。

## 3. 量化trade-off

{table}

![性能对比](performance.png)

![Tile存储与收益](tradeoff.png)

16不是最快，但相对8减少装载和排空；32把部分和容量从512B翻倍到1024B，
额外整网收益应与这个代价一起看。7用于非整除尾块验证，不作为默认配置。
32的尾块只有16个位置，不得称所有位置均有32倍复用。

默认tile16：psum 512B + integer tag FIFO 64B + 两个activation pack 8B = **584B主要数据存储**。
此外还有控制/计数寄存器、地址运算和MMIO读mux。容量只是RTL声明预算，未映射SRAM宏。
数组或integer是否被优化、选择网络是否成为关键路径，必须综合后才能回答。

## 4. 软件可见性能接口

新增只读偏移相对NPU_BASE：0x40总周期、0x44..0x58六层周期、0x5c权重行、
0x60输入事务、0x64退休事务、0x68最大在途、0x6c状态(valid/overflow/busy)。
VERSION为0x00010001；默认两种优化均关闭，参数通过subsystem→wrapper→top→conv。

接受start清零，统计START/WAIT状态边沿，包含层完成转移拍；完成冻结，clear_done
不清统计，非法start不改快照。计数32位饱和，越界递增置sticky overflow。
独立testbench按握手和状态对照，而不是读取计数器作为自己的参考。

层trace观察周期与硬件层计数边界不同，应看CSV的独立列：硬件C2比trace C2多1拍，
硬件六层之和等于总周期；trace另有CTRL残差。收益比较始终保持同口径，不混加。
主机装载周期单列，既不算核心周期，也不能称CPU/AXI吞吐。

## 5. 验证证据

- 各层每个元素与独立整数golden bit-exact，检查地址覆盖、X/Z、重复/漏写。
- 三种调度和tile7/8/16/32；边角padding、变化的tap/channel权重、signed输入、
  非零bias、非平凡量化及极值；更多固定seed和类别数。
- Matrix气泡/反压、Conv输出反压、tile尾块、busy中reset/重新装载、连续不同任务；
  换权重前排空、tag信用和issue/retire守恒有定向断言。
- 官方MMIO回归保留；新增文件驱动复杂参数、input-only复用、换模型、IRQ、done-clear、
  busy拒绝、只读保护、类别/保留shift错误及每次profile验证。
- 完整trace和日志是生成产物，PR仅包含精简摘要和可复现源码；历史Git对象不是默认依赖。

具体数目与配置见 `accepted_summary.json`。默认完整入口不允许跳过单元测试；
smoke结果不能被报告生成器当成阶段验收。CI配置随PR提交，实际CI状态应以GitHub为准，
配置存在不等于已经绿灯。

## 6. 综合之前为何可以说“阶段完成”

本阶段是**固定模型RTL功能、协议和调度验证**，不是芯片完工。

| 门槛 | 证据/范围 |
|---|---|
| 合同冻结 | 固定形状、NHWC、Q31、shift[-31,31]、mask、支持descriptor与fallback |
| 数值 | 完整网络和正式MMIO对同一独立golden，通过有限回归 |
| 协议 | 无丢失/重复，tag回收、排空、反压、reset/restart与尾块测试通过 |
| 优化机制 | C2 issue数不变，装载行符合4×18×ceil(80/T) |
| 性能 | 同阵列/数据/存储/边界，trace解释周期收益，达到设定周期目标 |
| 资源与接口 | 额外容量和端口明确，profiler经正式MMIO可读 |
| 复现 | 新目录一键流程、seed/工具/源码和结果hash；远端CI另行报告 |

因此我们可验收“限定合同下的RTL阶段”。综合要检查映射和时序，并不会替代此前
数值/协议验收。尚未完成的同步SRAM、PPA、实际时钟、物理实现、真实模型准确率与
整SoC验证，是下一阶段门槛，不写成已经完成但没有展示的数据。

## 7. 复现

```sh
python3 hardware/npu/scripts/run_tinycnn8_regression.py --output /新的空目录
python3 hardware/npu/tools/build_tiled_report.py /该结果目录 --output docs/kws_tinycnn8_tiled
swift hardware/npu/tools/render_tiled_charts.swift docs/kws_tinycnn8_tiled/performance_summary.csv docs/kws_tinycnn8_tiled
```

仿真只需Python、Icarus、vvp，不需要训练包或Swift。图表生成使用macOS AppKit，与CI回归无关。
本次工具：{summary['tools']['iverilog']}。源base：{summary['git_head']}，源hash在摘要。
"""
    (output / "REPORT.md").write_text(report)
    thoughts = f"""# 个人贡献与设计思考（供汇报采用）

这是对实际工作及设计取舍的表达，不替代团队作者记录，不声明独立完成整个NPU。

## 我的工作边界

我依赖已有Matrix/Vector、TinyCNN和SoC基础，重点负责模型定向验证、trace分析、
调度优化实验、软件可读性能指标及阶段验收证据。核心贡献不是“首次仿真”，而是
把观察、选择、实现和验证闭合起来。

## 观察—选择—代价—验证

1. **先观察，不先扩阵列。** 8输出通道匹配8列；C2占大部分计算，trace显示大量单事务等待。
   增加PE可能无法持续供数，因此先固定计算资源，研究调度。
2. **第一步选择低风险重叠。** gather/load可在独立读口下并行，已经证明11.12%整网收益，
   但装载次数不变，瓶颈转为等待。它既是成果，也揭示下一步上限。
3. **第二步选择空间复用。** 同一K组权重可服务多个位置，代价是必须保存多个INT32部分和，
   并正确关联返回结果；不能简单先发满所有输入再接输出，否则流水可能堵死。
4. **控制工作集，而非全层展开。** C2完整im2col约5760B；全层psum约2560B，Conv1全层psum10240B。
   小tile使活跃部分和有界，不代表行为存储已变成物理SRAM。
5. **不把最大tile当自动最优。** 比较8/16/32：默认16用584B主要数据存储，取得{speed:.3f}×整网周期速度比。
   32更快但存储更大，最终频率/面积可能改变最佳点，留待综合。
6. **不靠改数学获得快。** bias只一次、K间INT32、最终Q31双舍入不变；三路对独立golden。
   数据/权重恰零也不扣除有效工作，防止统计口径制造收益。
7. **把性能变成软件功能。** profiler使用显式事件端口，MMIO可读，独立monitor验证；
   以后换模型或存储仍能测，而不只是本次waveform截图。
8. **用出口定义完成度。** 达到数值、协议、机制、性能、资源预算和复现门槛，称RTL阶段完成。
   不声称PPA、真实准确率或整机已完成；这不是削弱成果，而是说明工程判断的边界。

## 可以直接说的贡献摘要

“我在团队已有计算模块上建立了独立逐层验证和周期级分析流程，根据trace定位Conv2
重复装载和单事务等待，设计了有界空间复用及回收调度。保持32个PE，tile16整网周期
减少{reduction:.2f}%，并将性能统计接入正式MMIO。我的关注点是收益、存储/控制代价和
验证完整度，而不是把仿真数字直接当作芯片PPA。”

## 不应说

从零原创全部NPU；真实KWS精度已验证；相对CPU有{speed:.3f}倍加速；584B就是面积；
固定TB时钟就是Fmax；test PASS就是所有输入形式证明；已完成同步SRAM和整SoC联调。
"""
    (output / "THOUGHTS.md").write_text(thoughts)
    (output / "TALK.md").write_text(f"""# 五分钟汇报与问答

**0:00–0:45 目标。** 固定KWS-TinyCNN-8，复用现有32PE阵列，研究模型定向调度而不是新增网络。
合成参数用于数值和性能验证，不报告语音识别准确率。

**0:45–1:30 发现。** C2占多数乘加；原调度对每个位置每K组都重载并等待。先做gather/load
重叠得到11.12%，但剩余等待仍很高，所以不能只继续优化数据准备。

**1:30–2:30 设计。** 改为一个空间tile共用一组权重，逐位置发射同时回收，以tag写回各自
INT32部分和。换权重必须全部退休。保持计算与量化不变；单字节读口仍限制供数。

**2:30–3:30 结果。** 图中tile16整网{b:,}→{o:,}，减少{reduction:.2f}%，速度比{speed:.3f}×；
C2速度比{c2speed:.3f}×。1,440输入不变，权重行5,760→360。各层golden及正式MMIO均通过。

**3:30–4:15 Trade-off。** tile16主要数据存储584B，包含512B部分和、64Btag、8Bpack。
32更快但部分和翻倍；容量不是面积，待综合验证端口/选择网络/频率。没有宣称能耗降低。

**4:15–5:00 完成度与个人工作。** 以合同、数值、协议、性能、资源预算、接口及复现作为RTL出口。
我的工作是trace驱动的选择、控制实现和证据链；团队基础不重复归功。综合/真实模型/整机是下一关。

## 问答

- **为何认为完成？** 是限定存储与模型合同下RTL阶段完成，不是芯片完成；所有出口有测试证据。
- **为何不多加PE？** 供数和重复装载先成为限制，固定资源才能隔离调度收益。
- **为什么不用32？** 测得更快但多512B部分和，频率/面积尚未知，16是有理由的初选不是全局最优。
- **真实KWS识别好吗？** 未验证；合成权重没有类别语义。
- **SoC是否全跑了？** 正式MMIO后端已测，CPU+AXI+DMA整SoC本次未跑。
- **面积/功耗改善多少？** 尚无综合/实现数据，权重流量下降不能直接换算功耗。
""")
    (output / "README.md").write_text("# TinyCNN-8 新阶段汇报材料\n\n[成果与trade-off](REPORT.md) · [个人设计思考](THOUGHTS.md) · [五分钟讲稿](TALK.md)\n\n图表和摘要来自通过完整验收的实测，不接受skip-units smoke。完整生成证据保留本地或CI artifacts。\n")
    print(f"Generated accepted report: {output}")


if __name__ == "__main__":
    main()
