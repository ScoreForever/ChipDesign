# 六场景 Demo 软件交付（2026-11-16）

基于合作者 `demo/fe167b1` 的统一 Tk 入口、MNIST 和 KWS 插件继续实现。
本轮无本地 FPGA，所有新增验收为软件、模型导出、原始数据前端或桌面检查。

## 已实现

| 场景 | 软件交付 | 当前边界 |
|---|---|---|
| MNIST | 保留画板/停笔逻辑；补全十类分数、周期、事件记录及无窗口图片输入 | 未新增实写/板测精度结论 |
| KWS | 保留麦克风/MFCC/四目标词动作；补全十二类分数、周期、记录与 WAV CLI | 未做新麦克风现场验收 |
| ECG | 发布样本、MIT-BIH 标注回放、精确训练前端、五类分数、暂停/结果导出 | 360 Hz、标注定位；未接 ECG 硬件 |
| HAR | 发布样本、UCI ZIP 读取、训练归一化、手机 CSV/实时跟随 | 手机佩戴、坐标和前端近似尚需现场校准 |
| 人脸 | 图片/摄像头；默认电脑 YuNet；另有 ABI 5 INT4 网格检测包及 NMS/坐标映射 | NPU 模型效果有限，未做实板/现场验收 |
| 表情 | 电脑裁最大人脸→INT4 七类模型；图片/摄像头、完整分数和时序 | FER2013 54.53%；实验模型，未现场验收 |

共享会话每个页面装载一次、同一页面复用模型；所有阻塞推理在同一 worker 串行执行。
离开页面取消轮询并排队释放连接。摄像头、麦克风、手机传感器均需现场用户主动开始。
窗口内容可以滚动，小屏幕不要求把六张卡片和所有控件同时放进一个固定高度窗口。

FPGA 后端调用现有 `Npu`/`JtagTransport`，失败不自动改为软件参考，也不重放不确定事务。
软件参考显示明确的后端来源，不生成“片上周期”。完整事件日志可用于未来逐输入板端对照。

## 本地验收

原始 JSON/日志在 [验证目录](validation/demo_software_20261116/)。

| 检查 | 结果 |
|---|---|
| 新增软件测试 | 13/13：前端、坐标映射、输入拒绝、真实本机 HTTP 采集、驻留模型、超时不重放、错误报告、发布文件哈希 |
| 既有 JTAG/RPC/性能测试 | 17/17，通过真实 Tcl 解释器执行批处理测试 |
| 六场景固定输入 | 80/80 完整 INT32 输出一致；包括新视觉包各 8 帧 |
| 原有七模型包回归 | 112/112 输出与黄金一致，当前编译镜像相同；不是 112/112 标签正确 |
| HAR 原始前端 | 官方原始 test 的 16 个黄金窗口，1152 个值/窗全部一致 |
| 原始 ECG/HAR CLI | MIT-BIH 100 的 3 拍、UCI test 前 16 窗完成软件推理 |
| 文件输入 | 合成数字、静音 WAV、合成手机 3 窗、空图及公开人脸图片两种检测/表情路径跑通 |
| 视觉导出复核 | 两份 QAT 状态重新导出，与发布 JSON 和 ABI 5 二进制逐项/逐字节一致 |
| 桌面 | Windows 实际打开选择页、ECG/HAR 页面，验证识别与导航；摄像头、麦克风和手机硬件未测试 |

软件测试中的模拟 NPU 只检查驱动调用和不重放语义，不能替代 FPGA 通过证据。
原始数据推理成功与黄金逐位一致是不同口径；验证 JSON 保留 `exact: null`/`true` 的区别。

## 模型与前端

视觉包与训练结果见 [模型说明](../software/host/demos/models/README.md)。
表情 FER2013 PrivateTest 3589 张准确率 54.5277%。
检测在 WIDER 派生的受控近景裁剪评估上 P/R/F1 为 49.5146% / 52.2184% / 50.8306%，
不是全图 WIDER AP；两者仍是实验基线，尚未达到可靠现场演示的识别质量。
电脑默认 YuNet 框选与 NPU 检测明确分开。

HAR 复用训练时全部 7352 个 train 窗口的逐轴均值和样本标准差；
手机近似前端做时间戳、SI 单位和轴映射检查，再构造九轴窗口。
ECG 精确复用训练时的 256 点裁窗、2×平均和每拍总体标准差归一化。
手机前端尚未证明与 UCI 原始采集等价，不能直接引用 HAR 数据集准确率作为现场成绩。

## 启动与接板

```sh
python -m pip install -r software/host/demos/requirements.txt
python software/host/demo.py --backend reference
python software/host/demo.py --self-test --output build/demo_acceptance.json
```

原始数据、YuNet、手机采集、重训及 headless 命令详见 [桌面说明](../software/host/demos/README.md)。
接板后按既有说明烧录 ABI 5 镜像，启动 OpenOCD，然后使用 `--backend fpga`；
可先执行 `demo.py --self-test --backend fpga --output build/board_demo.json`，
核对 80 个固定输入，再做各场景输入、模型切换和长时间驻留测试。
本轮未修改生产 RTL、约束或 bitstream，也没有将旧板测证据当作本轮演示通过。

新增 GitHub Actions 对 Windows/Linux 运行 demo 单测及无板 golden 检查。
手机浏览器安全上下文要求参考 [W3C Device Orientation and Motion](https://www.w3.org/TR/orientation-event/)；
采集服务不会代用户批准权限或跳过 HTTPS 检查。
