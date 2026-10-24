# SRAM 库现场调查与面积优化候选

调查日期：2026-10-20。通过用户授权的 SSH 连接，只读检查服务器上的 PDK 和课程公共模板。
本次没有修改远端文件、运行综合/布局布线，或改变本地 RTL。

## 已确认的宏

| 宏 | 组织/容量 | LEF 尺寸 µm | 宏本体面积 mm² | 判断 |
|---|---|---|---:|---|
| TS1DA32KX32 | 32768×32 / 128 KiB | 4444.505×1437.015 | 6.386820 | 超过目标 die 面积，长边也放不进 core |
| TS1DA64KX32R | 65536×32 / 256 KiB | 4761.695×2992.320 | 14.248515 | 不适合本次面积目标 |
| RA1SHD_2048x32M8 | 2048×32 / 8 KiB | 554.595×723.850 | 0.401444 | 可用于实际 A/B 评估 |

容量按字数×位宽计算；例如 32K×32 是 128 KiB，不是 32 KiB。
面积来自 LEF SIZE 与 Liberty cell area 交叉核对，不含 halo、供电与周边控制。

前两款位于：

```text
/lec/pdk/tsmc180/ts1da32kx32_100c/TSMCHOME/sram/
/lec/pdk/tsmc180/ts1da64kx32r_100c/TSMCHOME/sram/
```

已找到 Liberty（BC/TC/WC）、Verilog、LEF（含 6LM 版本）、GDS、SPICE 等视图。
TC Liberty 的 nominal voltage 为 1.8 V。它们是固定的已生成宏，不能裁掉未使用地址来缩小实体面积。

8 KiB 宏位于：

```text
/lec/lab4/soc_cv32e40p_template_pnr-stu/src/macro/RA1SHD_2048x32M8/
```

现场包含 TT/SS/FF Liberty、Verilog、vclef、antenna LEF、GDS 和 CDL。
核对得到同步单端口接口、11-bit 地址、32-bit 数据和 4-bit byte 写掩码。
Verilog 标注 generator 产品名称和当时生成配置，但目录中未发现可运行的 SRAM 生成器。
仿真模型的 BITS/word_depth 参数不能替代重新生成的 Liberty/LEF/GDS/CDL。

配套视图仍需工程核对：该 vclef 出现 METAL7/METAL8 层，而目前流程是 6-metal；
不能未经核对随意删除 obstruction。视图存在不等于已经证明它们的 release 和物理层定义完全一致。

## 搜索范围及限制

已遍历 `/lec/pdk/tsmc180` 的文件清单，并检查课程 `/lec/lab4`、`/lec/shared` 中
可识别的存储宏视图。未发现现成 1–4 KiB SRAM 或可识别的 SRAM compiler。
这一结论限于上述目录，不表示整台服务器或课程授权中一定没有生成器。
当前账户的 `~/chip-fullstack-2026`、`~/ai-design-2026` 为空，故没有在这些目录取得旧实验数据库。
未将工艺库、宏模型或私钥复制到项目。

## 推荐实验顺序

1. **低改动基线**：合并权重计算/调试查询读口，保留空闲查询、模型更新和主机协议。
   先比较同工艺综合面积，确认选择树是否减少。
2. **8 KiB 宏先只放权重**：替换现有 2 KiB FF 权重数组，保留激活 bank。
   用两次 32-bit 读取组装 64-bit 权重行，修改预取、返回 valid 和 bank_ready。
   宏自身虽有容量空余，仍可能比 FF 数组及读 mux 更省，但目前没有净面积实测。
3. **共享宏变体**：权重与激活总量约 3 KiB，可以共用一颗宏；但单端口无法直接承接
   并发权重预取、激活读取和输出写回，需仲裁、小缓存及背压。Conv1 跨 32-bit word
   的连续 byte 读取也需要处理。应与“只替换权重”比较面积、周期和时序，而非只看容量。
4. **无需新宏的补充路线**：真正支持 tile8/tile4，收窄 PE 局部和，研究无偏窄 psum
   加末组 INT32 bias。保留 signed MaxPool 与溢出语义，不直接截短带 bias 的累加器。

若之后取得合法可用的 compiler/小宏全套视图，优先评估约 2 KiB 的权重宏
（如 256×64）和约 1 KiB 的激活存储，按端口/带宽选择 bank 组织。
现有 128/256 KiB 大宏无需进入本轮实验。

引入 SRAM 不需要恢复 CPU、AXI 或旧 DMA；可通过 NPU 存储 wrapper 直接管理。
最终判断标准是宏本体、halo、外围与剩余标准单元的总物理占地，以及约 1700 µm core
的布局布线闭合情况。当前未产生新的面积、性能或 2×2 mm² 达标结果。
