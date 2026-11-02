# INT4 50 MHz 实板 CDC 与复位验证（2026-11-02）

本轮重新下载前次时序通过的 50 MHz 镜像；SHA-256 为 `BCEC1C7476F1D2423144D9E25C7D188B484C4CA1DCEDDB71B18305B00F3DCDCA`，Hardware Manager 下载完成、DONE=1、CRC=0。配置线为 Xilinx/1234-tulA；独立 FT232H fabric TAP 为 0403:6014、序列号 210512180081，OpenOCD 识别 IDCODE 0x00000001，TCK 1 MHz。

## 自动实板检查：PASS

测试程序 `verification/fpga/nocpu_board_cdc.py` 使用 64×64×1 输入、16 层 Conv3×3 + Requant 融合程序。输入/期望/实际二进制、模型包、逐条 RPC 日志和结构化报告存放于 `automated/`。

| 检查 | 实际观察 |
|---|---|
| 确认 busy 后暂停 TCK | 状态 0x03（busy、model_ready），停止请求扫描至少 1 秒后状态 0x0a（done、model_ready）；完整输出逐字节匹配软件参考 |
| busy 时重复 START | 先读回 busy，随后 START 返回错误 4（busy） |
| busy 时协议 RESET | 先读回 busy，随后 RESET；busy、done、fault、model/input ready、error 全部清零 |
| watchdog 超时与恢复 | 设置 1000 周期，运行后 faulted=true、error=10；协议 RESET 后状态清零 |
| 恢复后完整推理 | 再次上传并运行相同工作负载，输出匹配软件参考；测试结束恢复空闲 |

为避免 Windows RPC 往返使短任务在读状态前完成，START、busy 状态读取及紧随其后的测试命令通过单个 OpenOCD RPC 批量扫描。初始非批量尝试因读到已完成状态而失败，没有计为通过。复现时输出目录必须不存在：

```powershell
python verification/fpga/nocpu_board_cdc.py --output build/cdc_board_fresh
```

暂停区间不发送任何 RPC 或 runtest/scan 请求。OpenOCD 为无 CPU target 的专用 TAP；本轮未用示波器测量 TCK，因此停钟证据为适配器命令序列和时间间隔，不包含电气波形。此测试验证运行中停 TCK 后恢复通信；不等同于在 Shift-DR 中途停钟。

## 物理复位：PASS（用户现场操作）

脚本 `nocpu_physical_reset.py arm` 在空闲状态写入 timeout 寄存器哨兵 123456789，并读回任务计数 7。随后停止 JTAG。用户先表示不能确认按键；核对[厂商 EGo1 手册第 5.1 节](https://e-elements.readthedocs.io/zh/ego1_v2.2/EGo1.html)确认 P15 对应 RST/S6，PROG/S5 用于擦除配置，再提示操作。用户在本次对话明确确认“已按 RST/S6 并松开”。

`check` 发送 TAP reset 重新同步扫描选择，在观察复位状态前没有发送协议 RESET 或重编程。实测 timeout=10000000、job=0，busy/model_ready/input_ready/done/faulted 全部为 false，error/error_op/cycles/issues/writes 全部为零。随后上传并运行 17 元素 packed INT4 ReLU，新推理输出与期望均为 `000061070010760007`。确认输出后才发送协议 RESET 清理。详见 `physical/arm.json`、`physical/check.json` 和 `physical/commands.jsonl`。这证明空闲状态停 TCK 期间按键复位后的恢复；没有声称覆盖忙时按键复位或测得复位边沿波形。

## 边界

本轮不改变 RTL 或时序实现。109 条 CDC-15 原始结构警告保持，其端点/约束与动态审查见前一份 CDC 复核报告。数字与功能实板测试不能证明亚稳态 MTBF；实际断电恢复、温压和电气检查未执行。物理复位与 busy 时协议 RESET 是不同测试，不合并计算。
