# ChipDesign 系统工作原理动画

直接双击本目录的 **index.html**，使用 Edge、Chrome 或 Firefox 打开。页面完全离线，不需安装 Python、启动服务器或连接板卡。请保留同目录的 `style.css`、`app.js`、`data.js`；也可打开可独立分发的 `standalone.html`。

共 11 章、86 个教学步骤，以及两套当前 RTL 的逐周期数据：

1. 默认实例树、时钟复位、主机/片上分工。
2. 模型/输入装载、验证、START、读结果、ACK、RESET 生命周期。
3. JTAG 比特顺序、TAP、请求/响应邮箱及 toggle CDC。
4. 真实 64 B 描述符、字段与执行状态机。
5. 卷积窗口、padding、四元素 gather、权重打包。
6. 4×8 MAC、激活偏斜、纵向部分和、双权重 bank、11 级输出对齐。
7. bias、结果槽、四级 Q31 量化、ReLU/Pool 融合、credit。
8. 两颗 8 KiB 单端口 SRAM、64 B 缓存、两种 8 项队列、转发、背压、flush。
9. 五类逻辑算子的真实示例张量及 FC 点积。
10. 当前 RTL 的 544/3304 周期回放，逐拍握手、地址、数值、队列占用及跳转。
11. 计数器、逐算子摘要、可选波形、异常、LED 与验证边界。

## 操作

- 左侧切换章节；下方播放/暂停、前后步进、进度拖动、速度和重播。
- 页面空白处按空格播放/暂停，方向键单步；在按钮、输入框或选择框内保留其原键盘操作。
- 卷积章点选 8×8 输入网格，切换 K 组；阵列章点选 PE 查看对应整数乘法。
- 描述符章点选 PC，查看实际打包字；多章支持切换融合/独立编译。
- 回放章可跳到下一次 MAC 发射、写握手或描述符起点；算子时间条也可点击。
- “源码与证据”列出来源、快照哈希和证据边界；组件可点击查看职责。
- 窄屏采用竖向布局；大阵列/框图在画面内部横向滚动，避免文字压缩。系统减少动态效果设置会关闭流线动画；离散步骤仍可播放或手动切换。

## 事实来源

默认路径以 `hardware/soc/sim/filelists/chipdesign_nocpu.f`、`my_soc_top.sv` 及其当前实例树为准，源代码为 `exp/no_cpu` 的 `edcb86e` 双 SRAM 版本。没有把旧 CPU/AXI 路径或历史固定 TinyCNN 架构画入当前生产路径。

示例来自仓库 `software/host/make_demo.py`，seed=23：

`INT8[8,8,1] → Conv → Requant → ReLU → MaxPool → FC → INT32[1,1,3]`

| 本次重新运行 | 融合 | 独立 |
| --- | ---: | ---: |
| 描述符数 | 2 | 5 |
| 模型包 | 820 B | 1060 B |
| 激活分配峰值 | 128 B | 1344 B |
| 核心执行周期 | 544 | 3304 |
| 全部物化张量逻辑写握手 | 67 | 835 |
| 最终输出 | `[-900, -571, -231]` | `[-900, -571, -231]` |

独立 Python 整数参考负责逻辑语义；现有 `tb_npu_program.sv` 核对每次物化写回的地址、mask、有效 data，以及最终输出。`capture.sv` 是额外的被动仿真观察器，没有改变 RTL 或原有测试台。

教学框图、队列情境和阵列单次 issue 的逐级图均明确标记为示意；RTL 回放使用实际仿真信号。时钟沿前采样与片上 trace 的约定一致，未知值保留为 `X`，无效事务不展示地址/数据。完整仿真 CSV 不受片上波形 RAM 的 1024 个样本限制；额外采集的写 payload、MAC 组和等不代表现有片上 trace 具备这些信号。

本次实验使用原生 ready/valid 命令接口、同步行为 SRAM、测试周期 10 ns；展示以核心周期计，不把它称作 50 MHz 板上时延。核心统计不包含模型预验证、主机上传/下载、控制器发布完成和 ACK。两套仿真各执行一次，不是项目完整回归的冷/热两次实验。

项目既有的双真实宏、70 场景回归与 FPGA 布线结果引用 `docs/NPU_DUAL_SRAM_IMPLEMENTATION_20261026.md` 和对应 JSON，属于各自冻结证据。本动画没有重新运行 ASIC 综合/PnR/STA、FPGA 布线、bitstream 或板上推理。

## 重新生成与校验

从仓库根目录运行（Python 标准库、`iverilog`、`vvp`）：

```powershell
python docs/animations/npu-system/generate.py
```

生成或覆盖 `build/system_animation/` 下的演示模型、命令、全部写回黄金事件、仿真日志与 CSV；刷新本目录 `data.js`、`provenance.json` 和 `standalone.html`。源文件 SHA-256 写入 provenance，用于识别内容陈旧；页面本身不会在运行时重新读取 RTL。

浏览器交互检查（需要 Playwright 包，默认使用已安装 Edge；可用 `NPU_BROWSER_CHANNEL` 选择其他已安装渠道）：

```powershell
node docs/animations/npu-system/check.cjs <playwright-package-path>
```

检查全部教学帧与 3848 个实际执行周期、播放/暂停、跳转、切换、卷积窗口、PE 选择、证据弹窗、桌面/390 px 窄屏、暗色与减少动态效果。结果与截图写入 `build/system_animation/`；不修改生产 RTL。

页面、采样器与生成脚本未包含私有 SRAM 工艺模型、网表或工艺库。
