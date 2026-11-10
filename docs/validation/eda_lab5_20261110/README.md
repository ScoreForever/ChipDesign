# 2026-11-10 Lab 5 EDA 验证记录

流程依据：[课程 Lab 5：Signoff & Tapeout](https://full-stack-ai-chip.tianyuj.com/lab-5/)。本轮目标为整合后 ABI 5.0、8×8 NPU，不使用旧 CPU 或 ABI 4 的版图冒充当前结果。

## 环境已配置

连接：`ssh soc`。工艺：`/lec/pdk/tsmc180`；课程规则：`/lec/lab5/layout`；工具与课程环境：`/opt/eda/env.sh`、`/lec/eda.env`。只建立项目级环境，不改共享工具、工艺库或旧工程。

当前 PnR/Lab 5 工程根目录：

```text
/home/s2400012892/chip-fullstack-2026/ChipDesign_abi5_20261110_8c16462
```

在服务器开启新终端后：

```bash
source ~/chip-fullstack-2026/ChipDesign_abi5_20261110_8c16462/env.sh
cd "$CHIPDESIGN_ROOT"
```

Genus 综合在先前的 `ChipDesign_abi5_20261110_7fd9789` 冻结目录执行。两份快照的生产 RTL 完全相同，见 [哈希清单](production_rtl_sha256.json)。后者保留原综合过程；新目录仅在 RTL/约束、库、完成凭据、交付物哈希、时钟与 CDC 核对通过后导入原网表，绝不重写网表冒充新综合。

| 环境检查 | 结果 |
|---|---|
| 18 份工艺／SRAM 视图与源路径 | PASS |
| Genus 19.17 实际许可证与综合启动 | PASS；综合仍在运行，非综合完成结论 |
| Innovus 21.16 批处理启动 | PASS |
| Xcelium 21.09 编译及仿真 | PASS |
| Virtuoso IC618 批处理、工艺库与 Seal Ring 读取 | PASS |
| V2LVS 小模块转换 | PASS |
| Calibre 2022.3 DRC 引擎／许可证 | PASS；只执行环境专用的 1 条规则，不是芯片 DRC |
| 项目 OA 映射／规则工作副本 | 已准备，规则引用 `/lec`，无错误的 `/home/almalinux` 项目路径 |

原始数据见 [环境清单](environment/environment_manifest.json)、[输入预检](environment/preflight.json)、[Seal Ring 只读检查](environment/seal_source_inspection.log)。工艺规则和库文件仅保存在服务器私有工程中，未加入仓库。

## 进行中的实施与验证

- 综合：正在执行，尚无完成验收结论。
- PnR：等候同源综合交付物；将按实际标准单元面积选择 60%/65%/70%/75% 中能满足 1950 µm die 上限的最低目标密度。两宏采用相向布局、26 µm halo；不能满足面积上限时停止并保留失败原因。0.12 ns hold 修复目标不代表最终 hold 签核余量。
- 当前共享 SRAM bank 0/1 使用明确的物理角色映射，不再按两个独立 store 的旧名称匹配；39 项本地流程测试通过，仍须真实工具确认。
- SDF、独立 STA、几何 DRC、LVS/ERC、最终 GDS 天线和 Wire Bond PAD：待匹配版图及网表生成后执行。

## 验收边界

最终几何 DRC、LVS/ERC、天线及 WB 必须指向同一个最终 GDS，并关联 CDL、源快照、配置和输入哈希。课程 DRM/ESD/MOM/LUP 四类教学排除需单列原始计数；不得扩大为全部前端层豁免。IO 的 PR 电阻偏差须核对模型与实际数值，不能用旧脚本的宽泛容差或单项属性覆盖掩盖问题。SRAM 不作为 Box 跳过 LVS。

保留原始 ERC 残项并区分真实连接错误与待评审原库项；不会把工具退出 0、环境 smoke 成功或旧版报告写成流片签核通过。上述条目尚未完成，正式投片还需要多角时序、功耗/IR/EM、封装及人工复核等独立证据。
