# ChipDesign 桌面 Demo

统一入口现在包含手写、语音、ECG、HAR、人脸框选和表情分类六个页面。以下命令均从仓库根目录运行。
使用 Python 3.10+；无板软件验收见 [2026-11-16 交付记录](../../../docs/DEMO_SOFTWARE_20261116.md)。

```sh
python software/host/demo.py --list
python software/host/demo.py --backend reference
python software/host/demo.py --self-test --output build/demo_check.json
```

`--self-test` 默认使用软件整数参考，逐项核对六个场景的 80 个发布输入的完整 INT32 输出。
它不打开窗口、不访问摄像头/麦克风、不下载数据，也不需要 torch。输出必须是新文件。
`--backend fpga` 明确选择 JTAG；连接失败不会自动改用软件结果，超时不会自动重放 START。

安装运行依赖：

```sh
python -m pip install -r software/host/demos/requirements.txt
```

界面需要带 Tkinter 的 Python。Windows 上可用包含 Tcl/Tk 的 Python，Ubuntu / WSLg 上可安装 `python3-tk`。模型训练依赖见 [模型库依赖清单](../../zoo/requirements.txt)。

## 在 Windows 手动烧录和运行

1. 把 EGo1 的 `JTAG&UART` USB 和外接 FT232H 的 USB 都接到 Windows，保留 FT232H 到板上的 JTAG 接线。打开 Vivado Hardware Manager，选择 **Open Target → Auto Connect**。在板载配置线 `1234-tulA` 下右击 `xc7a35t_0`，选 **Program Device**，载入 ABI 5 的 `fpga_top.bit` 并点击 **Program**。配置断电后需要重下。镜像构建和接线见 [上板说明](../../../verification/fpga/NOCPU_BOARD_CHECK.md)。
2. 首次配置时，解压 [xPack OpenOCD 的 Windows 发行包](https://xpack.github.io/dev-tools/openocd/install/)。如果外接 FT232H 仍使用 FTDI 串口驱动，在 [Zadig](https://zadig.akeo.ie/) 里点 **Options → List All Devices**，按 USB ID `0403:6014` 选中外接红盒子，将其驱动设为 **WinUSB**。板载配置线 `0403:6010` 保留原驱动。更换红盒子的驱动会影响使用原 FTDI D2XX 驱动的程序。
3. 在 Windows PowerShell 的仓库根目录运行下列命令，把示例路径换成实际解压位置。确认日志出现 `npu.tap`、IDCODE `0x00000001` 和 Tcl 端口 `6666`，保持该窗口运行。

   ```powershell
   & 'D:\Project\OpenOCD\bin\openocd.exe' -f .\software\host\lab6_ft232h.cfg -f .\software\host\nocpu_openocd.cfg
   ```

4. 另开 Windows **Anaconda Prompt**，进入仓库根目录，启动原版入口并打开想演示的页面。

   ```bat
   conda activate ChipDesign
   python .\software\host\demo.py
   ```

## FPGA 手写数字识别

烧录 ABI 5 INT4 的 FPGA 镜像并接好 NPU JTAG。用独立终端启动 OpenOCD，下例使用项目的 FT232H 配置。接线和镜像准备见 [上板说明](../../../verification/fpga/NOCPU_BOARD_CHECK.md)。

```sh
openocd -f software/host/lab6_ft232h.cfg -f software/host/nocpu_openocd.cfg
```

打开 Demo 选择窗口，或者指定 `mnist` 直接进入手写识别。OpenOCD 的默认地址为 `127.0.0.1:6666`，可用 `--host` 和 `--port` 指定。

```sh
python software/host/demo.py
python software/host/demo.py mnist
```

按住鼠标左键写一个数字。松开鼠标约 450 ms 后，程序裁剪笔迹、等比缩放并居中到 28×28，按 `mnist_l` 的训练参数量化成 392 字节 INT4 输入。页面显示量化前的灰度图、板端分类和主机推理往返时间。切换页面后再次进入会重新加载模型，同一页面内连续识别复用已加载的模型。

遇到连接错误时，检查板卡和 OpenOCD 后点「重新连接」。若报告超时或状态不确定，先按 [主机协议说明](../README.md#对照与错误处理) 确认板端状态，必要时在板卡空闲时执行 `python software/host/nocpu_jtag.py reset`，再重新连接。

## FPGA 语音关键词识别

Linux / WSLg 的麦克风录音还需要系统 PortAudio 库，例如 Ubuntu 的 `libportaudio2`。

```sh
python software/host/demo.py kws
```

点击「录音 1 秒」，说出 `yes`、`no`、`up` 或 `down`。也可以打开 16 位 PCM WAV，程序使用音频的第一秒，较短的输入补零。主机将音频转换成 16 kHz 单声道、提取 49×10 MFCC 并归一化，随后将 245 字节 INT4 特征送板。界面显示声音波形、预测词和推理往返时间。

直接复用现成的 `kws_l` ABI 5 包。模型输出 10 个词及 unknown、silence，界面只有在预测为四个目标词之一时显示对应动作。归一化常数随 Demo 提供，运行时无需下载训练集或训练模型。

归一化常数按 [训练数据加载流程](../../zoo/data.py) 复算，使用 36,921 段训练音频和训练时每批 4,096 段共用的 80 dB 动态范围截断。实时推理按单段录音计算截断值。复核的 14 条发布黄金样本在使用训练批次截断时量化值逐项一致，使用单段截断时预测类别一致。

## 无板预览

```sh
python software/host/demo.py mnist --backend reference
python software/host/demo.py kws --backend reference
```

软件参考模式运行同一模型的整数参考实现，界面标记为「软件参考 · INT4」。

## ECG 与 HAR 回放

```sh
python software/host/demo.py ecg --backend reference
python software/host/demo.py har --backend reference
```

两个页面默认加载仓库中已量化的黄金输入，支持单条识别、下一条、暂停/连续回放和保存 JSON。
页面显示标签、波形、全部原始类别分数和主机耗时；FPGA 模式另外读取完成任务的片上周期。
分数不是概率。软件参考不会生成板端周期。

需要原始数据时显式下载（约 63 MB；不会在打开页面时自动下载）：

```sh
python software/host/demos/fetch_assets.py signals
```

ECG 点「打开 MIT-BIH」，选择 `build/demo_data/mitdb/100.hea`，同目录保留 `.dat` 和 `.atr`。
按标注 R 峰取 `[R-96:R+160]`、两点平均到 128 点，再按每拍总体标准差归一化和 INT4 量化，
与 `software/zoo/data.py` 一致。仅接受 360 Hz；每次最多读 200 拍。回放间隔遵循相邻 R 峰时间，
后端较慢时顺延。显示 AAMI N/S/V/F/Q 五类，属于工程演示。

HAR 点「打开 UCI HAR」，选择 `build/demo_data/uci_har.zip`，也可选择原始 UCI 数据 zip。
直接读官方 test split 的九轴窗口，顺序是 body_acc xyz、body_gyro xyz、total_acc xyz，
输入按时间优先排列为 128×9。归一化常数从全部 7352 个训练窗口复算，不使用测试集统计。
16 个发布黄金窗口已从原始 test 文件逐元素复核。官方窗口之间可能跨参与者，回放不表示连续现场记录。

复算归一化常数需训练环境的 torch：

```sh
python software/host/demos/prepare_signals.py build/demo_data/uci_har.zip --output build/har_stats.json
```

## 手机 IMU 采集与 HAR

启动一个只写新 CSV 的本机接收端：

```sh
python software/host/demos/phone_capture.py --output build/phone_session.csv
```

Android USB 调试环境可先执行 `adb reverse tcp:8765 tcp:8765`，再在手机浏览器打开终端输出的 localhost URL。
局域网接入时用 `--bind 0.0.0.0 --cert server.pem --key server.key`；将输出 URL 的主机部分换为电脑局域网地址，
使用手机已信任、包含该地址的 HTTPS 证书。传感器 API 要求安全上下文；程序不会绕过浏览器证书或权限检查。
令牌只供本次采集使用。手机页面点击「开始采集」才请求传感器权限，停止后会先发送已采集的数据，再开放下一轮。

在电脑 HAR 页面点「跟随手机 CSV」，选择接收端输出文件；也可用「导入手机 CSV」回放已结束的采集。
CSV 列为 `time_s,ax,ay,az,gx,gy,gz`，单位分别是秒、m/s²（含重力）和 rad/s。
接收端将浏览器的 beta/gamma/alpha 角速度换为 xyz rad/s；检查令牌、同源、包长度、数值和时间戳，
拒绝重复/倒序包，不把缺失数据默认为零。

前端对齐到 50 Hz，经过中值/20 Hz 低通、0.3 Hz 重力分离，构造九路输入。
窗口 2.56 秒，50% 重叠；加滤波预热后首窗约 3.1 秒。跟随模式处理最近 2,000 行，并显示最新完整窗口。
遇到超过 100 ms 的时间戳缺口时，跟随模式会从断点后重新积累连续窗口。导入 CSV 时仍会报告缺口。
可用 `--imu-rotation rotation.json` 提供从手机到训练腰部坐标的右手 3×3 正交旋转矩阵。
必须固定佩戴方向。手机前端是近似实现，尚未与 UCI 的原始采集系统或参与者实测校准，不能套用数据集准确率。

## 人脸框选与表情分类

```sh
python software/host/demo.py face
python software/host/demo.py face --backend reference
```

`face` 默认将 INT4 近景检测模型加载到 FPGA。它输出 4×4×5 个 INT32 网格值，电脑负责图像预处理、候选框解码、NMS 和坐标映射。界面显示「FPGA · JTAG」，推理完成后显示片上周期。板卡或 OpenOCD 不可用时显示连接错误。无板时可显式使用 `--backend reference` 在电脑上运行同一个 INT4 模型。
检测模型与表情模型分页面加载，不会假定两个模型同时驻留。

显式使用 `--face-mode host` 可选择电脑上的 YuNet。这个模式和表情页面都需要下载小型 YuNet 模型（约 233 kB，校验 SHA-256）。

```sh
python software/host/demos/fetch_assets.py yunet
python software/host/demo.py face --face-mode host
python software/host/demo.py emotion --backend reference
```

表情页面先由 YuNet 找最大人脸并裁剪，再用 NPU/整数参考输出七类表情分数。
无人脸时不执行分类；每次只处理一个人脸。支持打开图片与手动启停摄像头，
不会在启动页面时自动采集。`--camera-index` 可选择设备，`--yunet` 可指定本地 ONNX 路径。

两份 INT4 包、训练日志、QAT 权重、黄金输入和哈希在 [models/](models/README.md)。
表情模型 FER2013 PrivateTest 为 **54.53%（3589 张）**；检测模型在受控近景裁剪评估中 precision **49.51%**、recall **52.22%**、F1 **50.83%**（IoU≥0.5）。
这两个实验模型的识别效果仍有限。INT4 框选适合脸占画面较大的近景，即使推理正常完成也可能没有检出框。

## 无窗口输入与证据记录

```sh
python software/host/demo.py ecg --input build/demo_data/mitdb/100.hea --limit 16 --backend reference --output build/ecg_raw.json
python software/host/demo.py har --input build/demo_data/uci_har.zip --limit 16 --backend reference --output build/har_raw.json
python software/host/demo.py kws --input command.wav --backend reference --output build/command.json
python software/host/demo.py mnist --input digit.png --backend reference --output build/digit.json
python software/host/demo.py face --input photo.jpg --output build/fpga_face.json
python software/host/demo.py face --face-mode host --input photo.jpg --output build/host_face.json
python software/host/demo.py emotion --input photo.jpg --backend reference --output build/expression.json
```

也支持各模型 `golden.json` 或打包 INT4 `.bin`；JSON 有期望输出时检查每个 INT32 值。
空图像、非法窗口或黄金不一致会返回非零状态。MNIST 图片按四角亮度判断是否反色，再使用画板的同一预处理。
报告的 `passed` 代表本次操作/黄金比较成功，不能作为现场识别准确率或全套板卡签核。
自测报告中的 `golden_coverage` 列出各场景的向量总数和实际比对数。全部场景的全部向量均完成比对时，`golden_complete` 为 true。
只有 FPGA 后端完成完整黄金自测且所有输出一致时，`fpga_validated` 才为 true。

任一页面可加 `--log build/demo_events.jsonl`：逐次保存输入 packed hex、模型哈希、输出分数、后端和时序，便于之后用固定输入对照实板。
推理往返不包括模型加载、采集或预处理；板端周期另一次被动读取，读取耗时单列 `counter_ms`。
FPGA 后端另记录 `transfer_ms`（输入、结果与相关协议操作）和 `compute_wait_ms`（启动到主机观察到完成）。
相机和 KWS 记录采集或读取时间 `capture_ms` 及推理前处理时间 `preprocess_ms`。
HAR 记录窗口预处理耗时，手机窗口的耗时包含本批次共享的对齐和滤波。
图片 headless 报告的 `total_ms` 包含文件读取和冷加载。GUI 同页面复用模型。

## 测试与模型重训

```sh
python -m unittest discover -s software/tests -p test_demos.py -v
python software/host/demo.py --self-test --output build/demo_check_new.json
python software/zoo/check_golden.py
```

训练依赖单独安装；GPU 可选择 CUDA 版 PyTorch。训练不会在启动 GUI 时执行。

```sh
python -m pip install -r software/zoo/requirements-demo.txt
python software/host/demos/fetch_assets.py fer
python software/host/demos/fetch_assets.py wider
python software/zoo/train_demo_vision.py emotion --fp-epochs 80 --qat-epochs 35 --output build/retrained_demo
python software/zoo/train_demo_vision.py face --fp-epochs 55 --qat-epochs 30 --output build/retrained_demo
python software/zoo/check_demo_models.py
```

训练输出必须是新目录；可用 `--vision-models build/retrained_demo` 在 GUI/CLI 选择新包。
训练循环用独立验证划分选 checkpoint，报告保留测试指标和实际数据范围。
公共数据下载固定版本并验证文件哈希；数据集、摄像头图片、手机采集和构建文件不提交。
数据来源与引用见 [模型说明](models/README.md)。

## 添加 Demo

在 `software/host/demos/` 新增一个模块，提供 `Demo(parent, app)` Tk Frame，然后在 `software/host/demo.py` 的 `DEMOS` 中登记命令名、界面名称、描述和模块名。阻塞的板端事务通过 `app.worker` 串行执行，界面更新留在 Tk 主线程。退出页面时取消界面回调，并在同一 worker 上排队释放板端连接。
