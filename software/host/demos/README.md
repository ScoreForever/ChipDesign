# ChipDesign 桌面 Demo

在仓库根目录运行。界面需要带 Tkinter 的 Python 和 Pillow。Windows 上可用包含 Tcl/Tk 的 Python，Ubuntu / WSLg 上可安装 `python3-tk`。

```sh
python -m pip install Pillow
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

## 无板预览

```sh
python software/host/demo.py mnist --backend reference
```

软件参考模式运行同一模型的整数参考实现，界面标记为「软件参考 · INT4」。

## 添加 Demo

在 `software/host/demos/` 新增一个模块，提供 `Demo(parent, app)` Tk Frame，然后在 `software/host/demo.py` 的 `DEMOS` 中登记命令名、界面名称、描述和模块名。阻塞的板端事务通过 `app.worker` 串行执行，界面更新留在 Tk 主线程。退出页面时取消界面回调，并在同一 worker 上排队释放板端连接。
