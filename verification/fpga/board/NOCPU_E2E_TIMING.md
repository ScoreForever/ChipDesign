# JTAG实板端到端计时

`nocpu_e2e_timing.py` 复用生产主机驱动，记录模型部署到推理完成、输出读回及ACK的时间。执行前先完成对应镜像的时序、CDC复核和人工下载，确认板卡处于独占测试时段、OpenOCD已启动、设备空闲且未装载模型。脚本不自动复位、烧录或重试。以下命令在项目根目录执行。

## 七模型与模型常驻测试

```bash
python3 verification/fpga/board/nocpu_e2e_timing.py \
  --models all --system-mhz 50 --repeat 3 \
  --output build/board_e2e_01
```

默认从 `software/zoo/pretrained` 读取各模型的 `model.json` 和 `golden.json`，按ABI 5.0、8×8编译。只测一个模型可用 `--models ecg`，多个模型用逗号分隔。`--frame-index` 选择golden帧，默认0。每个模型只装载一次，第一次推理标为 `first_after_load`，后续标为 `resident_repeat`；每次均与该帧golden输出逐字节比较。

也可以传入自己已经编译的模型包：

```bash
python3 verification/fpga/board/nocpu_e2e_timing.py \
  --package build/program --input build/input.bin --expected build/expected.bin \
  --system-mhz 50 --repeat 5 --output build/board_e2e_custom_01
```

输出目录必须是新目录。`--timeout 30` 控制RPC和推理等待超时。默认连接本机6666端口，可用 `--host`、`--port` 指定实际端点。

## 计时范围

| 阶段 | 计时边界 |
|---|---|
| model_capabilities | 驱动执行模型格式与硬件能力核对 |
| model_upload | MODEL_BEGIN、配置寄存器、模型镜像传输 |
| model_commit | MODEL_COMMIT及预验证返回 |
| input_metadata | 读取输入位置、长度并检查输入 |
| input_upload | INPUT_BEGIN及输入数据传输 |
| input_commit | INPUT_COMMIT返回 |
| start | START命令返回 |
| completion_poll | 完成状态查询，包括RPC延迟和驱动轮询间隔 |
| output_metadata | 读取输出位置和长度 |
| output_readout | 输出数据读回 |
| ack | ACK命令及驱动返回 |

使用主机单调高精度时钟记录连续阶段，每段包含实际Python/RPC/USB传输开销。各阶段相加对应本次装载或推理的主机耗时；不是对芯片内部各段延迟的单独测量。

`deployment_to_first_output_and_ack_ms` 连续计时从模型装载前到第一次推理返回，包含结果读回和ACK。`deployment_plus_first_inference_ms` 是装载与第一次推理两个测量区间的和；两者的差值为两区间之间的主机准备开销。连接建立、文件读写、模型编译、golden比较和额外性能寄存器查询均不计入这两个主要区间。

硬件计算时间另外通过 `active_cycles / system_mhz` 换算为毫秒。`--system-mhz` 是操作人提供的镜像频率，不是脚本实测时钟。主机等待时间与硬件计算会重叠，不能把硬件计算时间再加到端到端时间上。OpenOCD的 `adapter speed` 查询原文保存在报告中，仅代表配置读回，不是TCK波形测量。

## 输出与失败处理

- `report.json`：ABI、阵列、初始状态、模型/输入/golden/实际输出哈希、阶段耗时、24项硬件计数与逐次比较结果。
- `phases.csv`：逐模型、逐次运行的各阶段主机耗时。
- `model_XX/`：本次模型包、输入、golden和实际输出二进制。

任一输出不一致或命令超时后停止，报告 `passed=false`，保留已完成结果和失败阶段计时。不会自动重发START/COMMIT，不会自动清错；重新测试前先检查硬件状态，再使用新目录。下载镜像身份仍需另附编程记录，本脚本不读取FPGA配置帧来证明镜像身份。

## 传输方式对照

默认使用burst数据传输和批处理RPC。`--wordwise` 关闭burst，`--legacy-rpc` 使用逐次握手RPC，两项可以分别对照。每轮使用新结果目录，保持同一镜像、输入和TCK配置，并在两轮之间人工确认状态。不能把主机通信优化带来的加速写成NPU计算周期下降。

RTL端到端计时另见 `software/zoo/e2e_timing.py`。它统计仿真的JTAG扫描时间与TCK数量，不含真实USB/RPC开销；当前测试台系统时钟为100 MHz，不能直接当成50 MHz实板测量。两类报告需要分别标明平台、存储配置和时钟。

本脚本的测试入口：

```bash
python3 -m unittest discover -s software/tests -p test_npu_e2e.py -v
```
