# Task plan: isolate historical CPU/AXI/DMA/Debug RTL

**Goal:** 将不属于当前 no_cpu 主框架的历史 CPU/AXI/DMA/Debug RTL 放入独立历史目录，保留当前 no_cpu 构建路径可用，并让文档说明清楚。

## Phases

- [complete] 1. 盘点当前生产源文件与历史目录边界 → 形成 findings.md
- [complete] 2. 移动历史 RTL/测试资产到 `hardware/legacy/cpu_soc/` → `git diff --summary` 可审查
- [complete] 3. 更新入口/文档中的路径说明 → active filelist 与 no_cpu 脚本仍指向现有路径
- [complete] 4. 验证无 no_cpu 引用断裂并记录结果 → 静态检查与可用测试通过

## Decisions

- 当前 no_cpu 生产入口保留在 `hardware/npu/rtl`、`hardware/soc/rtl/control` 和 `hardware/soc/rtl/my_soc_top.sv`。
- 历史代码移动到独立命名空间，不混入当前 `hardware/soc/rtl` 主框架。

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| CPU 目录移动时因预建目标目录多出一层，首次修正路径不匹配 | 1 | 检查实际目录后移动至 `cpu_cv32e40p/`，清理空目录，最终 R100 校验通过 |

## Next Step

无；整理与验证完成，等待用户审查差异。
