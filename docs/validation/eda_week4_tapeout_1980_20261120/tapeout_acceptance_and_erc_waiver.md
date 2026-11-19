# Week 4 Tapeout Acceptance & ERC Waiver Memo / 流片验收与 ERC 豁免备忘录 (`2026-11-20`)

**Design / 设计**: `my_soc_top` · `2000 DBU/µm` · sealed die `1980.000 × 1980.000 µm`
**Status / 状态**: `formal_tapeout_signoff: false`. Every check below passes or has an exact count, and four ERC classes wait for a written Foundry/TA waiver. 所有检查均通过或已精确计数；4 类 ERC 仍待代工厂/助教书面豁免。

All numbers come verbatim from the receipts in this directory (`lab5_summary.json`, `final_erc_geometry.json`, `floorplan_geometry.json`, `delivery_manifest.json`, `metrics.json`, `mmmc_corners.csv`, `rc_sensitivity.tsv`, `sram_interface_timing.csv`, `cdc_4corners.tsv`, `dual_sdf_summary.json`, `prects_cells.json`, `final_cells.json`, `wirebond_keepouts.tsv`). Values marked *derived* are arithmetic on those receipts.
本文所有数值均逐字取自本目录回执；标注 *derived* 的数值为对回执的算术推导。

---

## 1. Executive Signoff Ledger / 流片验收总账

### 1.1 Die Geometry / 芯片几何

| Item / 项目 | Value / 数值 | Source / 来源 |
|---|---|---|
| Sealed die / 封环后芯片 | `1980.000 × 1980.000 µm` = `3,960,000 × 3,960,000 DBU` (limit `max_allowed_sealed_die_um: 1980.0`) | `lab5_summary.json` |
| `actual_postdummy_bbox_um` | `[-15.32, -15.04, 1964.68, 1964.96]` (equals `sealed_outer_bbox_um`) | `lab5_summary.json` |
| Seal Ring reference box / 封环参考框 | `[-0.32, -0.04, 1949.68, 1949.96]` | `lab5_summary.json` |
| Unsealed raw die / 未封环裸片 | `[0.0, 0.0, 1949.36, 1949.92]` = `1949.36 × 1949.92 µm` (`3,898,720 × 3,899,840 DBU`, *derived*) | `lab5_summary.json` |
| Floorplan area / 布图面积 | `3.801096 mm²` (`die_area_mm2: 3.801096051`) | `metrics.json` |
| Core area / 核心面积 | `2.715243 mm²` (`core_area_mm2: 2.715242880`) | `metrics.json` |
| Origin convention / 坐标原点约定 | Lab 5 convention kept: raw die lower-left at `(0, 0)`. The `15 µm` Seal Ring frame extends to negative coordinates. 保留 Lab 5 约定：裸片左下角为原点，封环外框延伸至负坐标。 | — |

Cell inventory / 单元统计: pre-CTS `53,126` std instances / `1,179,160.46 µm²` → final `62,246` / `1,307,516.00 µm²` (CTS + repair `+128,355.54 µm²`). Both stages have `2` SRAM macros, `802,887.18 µm²` total.

### 1.2 Timing / 时序 (all `0` violations / 全部零违例)

| Corner | Setup WNS (ns) | Hold WNS (ns) | TNS setup / hold | Violating endpoints |
|---|---:|---:|---:|---:|
| `ss125` | `0.533` | `0.592` | `0.0 / 0.0` | `0` |
| `tt25` | `7.965` | `0.306` | `0.0 / 0.0` | `0` |
| `ff0` | `11.368` | `0.167` | `0.0 / 0.0` | `0` |
| `ffm40` | `11.769` | `0.150` | `0.0 / 0.0` | `0` |

Limiting paths / 关键路径: `ss125` setup `i_control/i_npu/i_array_tag_pipe_reg[14][0]/CP` → `i_control/i_npu/results_reg[2][158]/D` (arrival `20.814`, required `21.347`). `ffm40` hold `i_control/i_npu/element_row_step_reg[8]/CP` → `i_control/i_npu/element_row_jump_reg[8]/D` (arrival `1.131`, required `0.981`).

**RC sensitivity / RC 敏感性** (`rc_sensitivity.tsv`):

| View | Corner | RC scale | Mode | WNS (ns) | TNS (ns) | Violating |
|---|---|---:|---|---:|---:|---:|
| `view_ss125` | `ss125` | `1.00` | setup | `0.533` | `0.0` | `0` |
| `view_ss125_rcp15` | `ss125` | `1.15` | setup | `0.236` | `0.0` | `0` |
| `view_ss125_rcp20` | `ss125` | `1.20` | setup | `0.340` | `0.0` | `0` |
| `view_ffm40` | `ffm40` | `1.00` | hold | `0.150` | `0.0` | `0` |
| `view_ffm40_rcm15` | `ffm40` | `0.85` | hold | `0.144` | `0.0` | `0` |
| `view_ffm40_rcm20` | `ffm40` | `0.80` | hold | `0.141` | `0.0` | `0` |

Setup slack does not fall monotonically with RC scale: `+15%` (`0.236 ns`) is worse than `+20%` (`0.340 ns`). The memo reports both values as measured. setup 余量随 RC 比例非单调，最差为 `+15%`，按实测披露。

**Dual SRAM interface / 双 SRAM 接口** (`sram_interface_timing.csv`, slack in ns; `bank[0]` = activation, `bank[1]` = model):

| Corner | Bank | setup_to | hold_to | setup_from | hold_from |
|---|---|---:|---:|---:|---:|
| `ss125` | `[0]` / `[1]` | `1.053` / `0.977` | `0.931` / `1.105` | `0.649` / `0.577` | `3.431` / `3.390` |
| `tt25` | `[0]` / `[1]` | `10.579` / `10.447` | `0.476` / `0.585` | `8.399` / `8.348` | `1.762` / `1.748` |
| `ff0` | `[0]` / `[1]` | `13.209` / `13.137` | `0.269` / `0.335` | `11.892` / `11.829` | `1.017` / `1.036` |
| `ffm40` | `[0]` / `[1]` | `13.560` / `13.484` | `0.248` / `0.308` | `12.277` / `12.222` | `0.960` / `0.983` |

**CDC audit / 跨时钟域审计** (`cdc_4corners.tsv`): `452` paths (`113` per corner × 4), `0` negative slack. 共 `452` 条路径零违例。

| Kind / mode | Paths (4 corners) | Min slack (ns) |
|---|---:|---:|
| `request` / late | `288` | `19.941` |
| `response` / late | `148` | `19.942` |
| `req_sync` / late · early | `4` · `4` | `18.327` · `0.297` |
| `ack_sync` / late · early | `4` · `4` | `998.355` · `0.292` (`ffm40`, overall minimum) |

### 1.3 Dual-Corner Post-Route Gate-Level SDF / 双角后仿 SDF

| Corner | MTM | `passed` | JTAG commands | Timing diagnostics | `SDFNDP` clamped negative IOPATH |
|---|---|---|---:|---|---:|
| `SS 125°C MAX` (`ss125_max`) | `MAXIMUM` | `true` | `41` | `false` (`0`) | `0` |
| `FF -40°C MIN` (`ffm40_min`) | `MINIMUM` | `true` | `41` | `false` (`0`) | `120,327` |

Disclosure / 披露: at `FF -40°C MIN` the simulator clamps `120,327` negative IOPATH delays to zero (`SDFNDP`). The clamp makes those arcs slower than the SDF value, and the gate-level run still passes all `41` JTAG commands. Both corners also report the same accepted, non-fatal items: `50,832` `IFNONE` arcs (XOR/XNR/FA/HA/MUX sum and select arcs), `5` time-0 input-pad conflicts, `6` SRAM negedge-period notices, and `64` unqualified SRAM CLK→Q IOPATHs. `fatal`, `unexpected_sdfnep` and `unexpected_sdfnet` are empty at both corners.
`FF -40°C MIN` 下 `120,327` 条负 IOPATH 延时被钳位为 0（`SS 125°C MAX` 为 `0`），仿真仍全部通过；上述可接受诊断两角一致，且无 fatal 或意外 SDF 告警。

### 1.4 Lab 5 Calibre Same-Source Checks / 同源物理验证 (`postDummy.gds` + `my_soc_top.cdl`)

| Check | Rules | Result / 结果 |
|---|---:|---|
| DRC | `523` | `passed: true`, `0` blocking. Raw `2,427` hier / `16,935` expanded, all in accepted teaching-guideline categories listed below. 无阻断违例。 |
| nmLVS @ `3.48%` poly-R tolerance | — | `CORRECT`, `12/12` ports, `0` property errors, `sram_blackboxed: false` (full transistor-level `RA1SHD_2048x32M8` SRAM / SRAM 全晶体管级比对，未黑盒) |
| nmLVS @ `0%` tolerance | — | `INCORRECT` on properties only: `45` property errors, max deviation `3.47%`, `12/12` ports |
| Antenna | `62` | `0 / 0`, `passed: true` |
| WireBond | `64` | `0 / 0`, `passed: true` |
| ERC | `12` | `7` hier / `977` expanded, `manual_review_required` (see §3 / 见第 3 节) |

**DRC accepted teaching categories / 教学可接受类别** (hier / expanded):
`ESD.24g` `30 / 210` · `ESD.25g` `2,384 / 16,688` · `LUP.3.3g_3.3V` `1 / 7` · `LUP.5.3g_3.3V` `10 / 28` · `MOM.R.2` `1 / 1` · `DRM.R.1` `1 / 1`. The sums match the raw totals (`2,427 / 16,935`, *derived*). 合计与原始总数一致。

**`CO.R.4` PIMP patch / PIMP 补丁** in `B776_POSTVDD2CDG`: the contact at `[52.23, 72.13, 52.45, 72.35] µm` had no PIMP coverage (`already_covered: false`). The patch adds PP at `[52.3, 52.91, 52.59, 73.795] µm` (`[104600, 105820, 105180, 147590]` DBU) and stops at the adjacent NP wall (`x = 52.59 µm`). The post-patch DRC above has `0` `CO.R.4` results, which verifies the fix. 补丁后 DRC 无 `CO.R.4` 结果，修复已验证。

**LVS tolerance root cause / LVS 容差根因**: the IO SPICE model is version `270a` and the GDS-side extraction is version `280a`. Each recorded sample deviation has nearly the same layout/source ratio (`3404990 / 3290710 ≈ 1.034728`, `787.162 / 760.744 ≈ 1.034727`, `1375190 / 1329040 ≈ 1.034724`, all `+3.47%`, *derived*). This uniform ~`3.47%` ratio across distinct resistor magnitudes, combined with zero port or topology mismatches (`12/12` ports, `0` non-property errors), is consistent with a sheet-resistance parameter delta between the `270a` SPICE and `280a` GDS IO library releases rather than a connectivity error. The `3.48%` tolerance sits `0.01` percentage points above the measured `3.47%` maximum and still requires the TA's written acceptance (§4). 各采样电阻的版图/网表比值均近似 `1.0347`（`+3.47%`）且无端口或拓扑错误，符合 `270a` SPICE 与 `280a` GDS 间方块电阻模型版本差的特征；`3.48%` 容差仅高于实测最大值 `0.01` 个百分点，仍需助教书面认可（§4）。

---

## 2. Formal Delivery Package Verification / 正式交付包校验 (`delivery_manifest.json`)

| File / 文件 | Bytes / 字节 | SHA-256 |
|---|---:|---|
| `design_and_reports.tar.gz` | `120,201,195` | `98995bbd773109016bb958ed3e0c28c14aed2ccd790a8599ebb77a1b46a8fc04` |
| `postDummy.gds` | `137,037,824` | `66007a54ba102331b779adc637989c7f1fdaca3cf304d9bfe6bf07573fc41771` |
| `my_soc_top.cdl` | `14,146,189` | `dc4427554d742585179032b6bf993f0fa431f6d7327ffbb7604d35f69604a68b` |
| `final_inputs.sha256` | `161` | `0c65b33216b58785323d7835d1306b9e4db267ed599c35303f6aa29d6452b269` |

Self-check flags / 自检标志: `gzip: true` (archive integrity test passed) · `sha256sums: true` (the four delivered files match the generated `SHA256SUMS`) · `final_inputs: true` (delivered `postDummy.gds` and `my_soc_top.cdl` match `final_inputs.sha256`).
The GDS and CDL checked in §1.4 are these exact files. §1.4 所检 GDS/CDL 即上表文件。

---

## 3. ERC Waiver Review Memo / ERC 豁免评审备忘录 (Foundry / TA)

**Totals / 总计**: `12` rules executed, `7` hier / `977` expanded (`832 + 72 + 1 + 72`), `status: manual_review_required`, `formal_tapeout_signoff: false`. The other `8` rules report `0`. 其余 8 条规则为零。

**Core boundary used for location proof / 位置证明所用核心边界** (verbatim from `floorplan_geometry.json`, extracted from `outputs/routed.def` `core7T` `0.56 × 3.92 µm` site rows, `420` rows): core width `1649.2 µm`, height `1646.4 µm` (`1649.2 × 1646.4 = 2,715,242.88 µm²`, matching `metrics.json` `core_area_mm2`), placed at `[150.08, 151.76, 1799.28, 1798.16] µm`. All std cells and both SRAM macros sit inside this box. 所有标准单元与两颗 SRAM 均位于该框内。

### 3.1 Marker Table / 标记明细 (`final_erc_geometry.json`, bbox `[x0, y0, x1, y1]` µm)

| Rule | Hier / Exp | Marker bbox (µm) | Physical location / 物理位置 |
|---|---:|---|---|
| `mnpg` — MOS connected to both power and ground | `4 / 832` | `[83.525, 948.23, 107.525, 948.41]`<br>`[83.525, 949.35, 107.525, 949.53]`<br>`[2.24, 1440.385, 26.24, 1440.785]`<br>`[2.24, 1441.535, 26.24, 1441.935]` | West IO ring. The `y ≈ 948–950` pair lies in the `W_vdd_1` (`PVDD1CDG`) pad span (bond pad `y 944.8–1004.8`). The `y ≈ 1440–1442` pair lies in the `W_vddio` (`PVDD2CDG`) pad span (bond pad `y 1436.115–1496.115`). Max `x = 107.525`, which is `42.555 µm` outside the core edge `x = 150.08`. |
| `floating.nxwell_float` — nxwell_float is not connected to POWER | `1 / 72` | `[1.575, 215.785, 18.995, 221.875]` | West IO ring, `W_vss_1` (`PVSS1CDG`) pad span (bond pad `y 207.83–267.83`). Max `x = 18.995`, `131.085 µm` outside the core. |
| `npvss49` — ntap connected to GROUND | `1 / 72` | `[1.975, 215.985, 18.595, 221.675]` | Same `PVSS1CDG` diode structure as `floating.nxwell_float`, nested inside its bbox. Max `x = 18.595`, `131.485 µm` outside the core. |
| `floating.psub_float` — psub_float is not connected to GROUND | `1 / 1` | `[-15.325, -15.045, 1964.685, 1964.965]` (hollow 15-vertex frame) | Seal Ring substrate moat. The polygon's inner cutout is an octagon `x 0.13–1949.23`, `y 0.13–1949.79` with `70.436 µm` corner chamfers (`70.566 − 0.130 = 70.436 µm`, *derived*), so the marker covers only the `≈15 µm` Seal Ring band, a `0.13 µm` die-edge sliver and the four corner-cell triangles. Its nearest approach to the core is `149.95 µm` (cutout edge `x = 0.13` / `1949.23` vs core edge `150.08` / `1799.28`, *derived*). |

Expanded counts are repeats of the same IO cells around the ring. This die has `8` `PVDD1CDG`, `4` `PVDD2CDG`, `4` `PVDD2POC` and `8` `PVSS1CDG` pads (from the `35` pad list in `wirebond_keepouts.tsv` and the per-side supply set), so `72 = 8 × 9` diode units per `PVSS1CDG` rule, and the `832` `mnpg` count follows the per-cell finger accounting in `docs/validation/eda_week3_lab5_20261119/erc_deep_dive.md`.
**Conclusion / 结论: zero ERC markers inside the active stdcell/SRAM core. Every marker is inside an unmodified vendor IO cell or the outermost Seal Ring substrate moat. 核心区（标准单元/SRAM）内 ERC 标记为零，全部位于未修改的厂商 IO 单元或最外层封环衬底隔离带。**

### 3.2 Root Cause & Waiver Rationale / 根因与豁免理由

1. **`mnpg` (`4 / 832`): rail ESD clamps / 电源轨 ESD 钳位管.** `PVDD1CDG` (`VDD↔VSS`) and `PVDD2CDG`/`PVDD2POC` (`VDDPST↔VSSPST`, subcell `B776_POSTVDD2CDG`) contain RC-triggered big-FET NMOS clamps whose drain and source tie to a power net and a ground net by design. The RC trigger holds the gate low in normal operation, so no DC path exists. *Waiver basis*: intended IO-library ESD topology, not a design short. 设计意图内的 ESD 拓扑，正常工作时栅极为低，无直流通路。
2. **`floating.nxwell_float` + `npvss49` (`1 / 72` each): ground-rail ESD diodes / 地轨 ESD 二极管.** Both rules flag the same `72` N-well islands and N-taps in `PVSS1CDG`. These are the cathodes of the antiparallel `VSS↔VSSPST` diodes and must tie to a ground net. Retying them to `VDD` would remove the protection. *Waiver basis*: intended vendor diode structure. 若改接电源将破坏保护功能。
3. **`floating.psub_float` (`1 / 1`): Seal Ring exterior substrate / 封环外部衬底.** The continuous IO ring and corner cells separate the outer substrate frame from the `VSS`/`VSSPST`-strapped internal substrate. A metal tie across the Seal Ring assembly isolation zone would break `SR.S.1` and the die-saw crack stop, so no synthetic tie was added. *Waiver basis*: mandatory Seal Ring isolation. 跨隔离带接地会违反 `SR.S.1`，故不加伪连接。

No virtual shorts, fake labels, or edits to vendor IO/SRAM/PDK cells were used to suppress any of the `977` results. 未使用虚拟短接、伪标签或修改厂商单元来压制任何 ERC 结果。

---

## 4. Pending Items Before Foundry Mask Release / 掩膜发布前待办

`formal_tapeout_signoff: false` stays in force until every item below is closed in writing. 以下各项书面关闭前保持 `false`。

- [ ] Foundry/TA written waiver of `mnpg` `4 / 832` (`PVDD1CDG`, `PVDD2CDG`, `PVDD2POC` rail clamps).
- [ ] Foundry/TA written waiver of `floating.nxwell_float` `1 / 72` and `npvss49` `1 / 72` (`PVSS1CDG` diodes).
- [ ] Foundry/TA written waiver of `floating.psub_float` `1 / 1` (Seal Ring substrate moat).
- [ ] TA written acceptance of the `3.48%` nmLVS poly-resistor tolerance for the IO `270a` SPICE vs `280a` GDS model delta (`45` property errors, max `3.47%`, at `0%`).
- [ ] Foundry/TA acceptance of the PDK ESD/LUP/MOM/DRM guideline categories (`ESD.24g`, `ESD.25g`, `LUP.3.3g_3.3V`, `LUP.5.3g_3.3V`, `MOM.R.2`, `DRM.R.1`; `2,427 / 16,935` total).

Independent PrimeTime STA, Formality LEC and 5-workload VCD power receipts are archived separately in this directory's `README.md` and are not restated here. 独立的 PrimeTime STA、Formality LEC 与 5 组负载 VCD 功耗回执另见本目录 `README.md`。
