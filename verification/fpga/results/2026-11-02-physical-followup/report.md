# 50 MHz INT4 忙时按键复位与断电恢复（2026-11-02，Asia/Singapore）

结论：忙时按键复位的功能采样检查通过，断电后重新下载同一镜像并恢复推理通过。电气波形未实测；用户确认现场没有示波器或逻辑分析仪。未修改 RTL、时钟频率或 Flash。

| 项目 | 结论 | 证据范围 |
|---|---|---|
| busy 期间 RST/S6 按键复位 | PASS，功能采样 | 最后有效状态 0x03 → TAP 应答中断 → 全局复位状态；没有边沿级波形 |
| 按键复位后新推理 | PASS | INT4 输入、FC MAC、INT32 输出逐字节匹配 |
| 全部断电、重上电、重新下载后恢复 | PASS | 用户灯灭确认、两组 USB 消失/重现、DONE/CRC、清零状态及新推理 |
| 上电从 Flash 自动加载该镜像 | NOT RUN | 本轮没有写 Flash，也没有验证自动加载 |
| TCK/复位/电源电气波形及亚稳态 MTBF | NOT RUN | 缺少测量仪器，软件日志不能替代波形或 MTBF 分析 |

## 忙时物理按键复位

用户按 RST/S6 约 1 秒后松开。`nocpu_busy_button.py` 连续执行合法程序，单次 OpenOCD RPC 合并 ACK、INPUT_BEGIN、输入写入、INPUT_COMMIT、START 和状态采样，以减少 Windows 往返造成的任务间空档。首次采到 done 时立即结束该轮采样。

最终工作负载使用 1×1×32 输入、16 层 Conv3×3 + Requant，前 5 层 pad=2，后续 pad=1；32 通道，最终输出 11×11×32。编译器分配的 activation 地址保持不变；相同的只读权重/参数块在模型镜像中去重，调整 descriptor 的 weight/parameter 地址后仍在 8KiB 容量内。共享只读块不改变计算语义，模型经硬件 MODEL_COMMIT 验证成功。完整源模型、包和输入见 `busy_button_r5/`。

最终记录：`last_valid_status=3`（busy=true、model_ready=true），随后 TAP 应答中断。等待按键松开并重新初始化 TAP 后，job=0，timeout=10000000；busy、model_ready、input_ready、done、faulted、error、error_op、cycles、issues、writes 全部清零。观测复位状态前未发协议 RESET、未重新配置 FPGA。用户在当前对话确认最终这次“已按 RST/S6 并松开”。

这是“复位前最后有效采样为 busy，复位后清零”的功能覆盖，不能由此确定模拟复位边沿与 busy 的精确相对时刻，或证明中间没有未采样的完成事件。`busy_button_r5/report.json` 明确保留 sampled scope；边沿对齐需要外部采集或经单独时序验收的 ILA。

此前三次人工按键均落在输入上传/任务间或最后采样已 done，未计为 busy 覆盖；另有一次仅在主机编译阶段发现 activation 容量超限，未触发实板测试。`busy_button/`、`busy_button_r2/`、`busy_button_r3/`、`busy_button_r4/` 保留这些未覆盖/失败尝试。首轮恢复检查因按键仍按住而超时；最终脚本在重新初始化 TAP 前等待 2 秒。

按键复位后的独立新推理见 `after_busy_reset/`：检查初始状态及 job 全零后，加载 FC 模型并比较 12 字节 INT32 输出，实际与期望均为 `fbffffff0c000000dcffffff`，随后才执行协议 RESET 清理。

## 断电与恢复

1. 关闭 OpenOCD、释放 WSL USB 转发。
2. 用户断开开发板全部供电/配置 USB 和 FT232H USB，保持信号线原接法，确认“已全部断电，灯已熄灭”。`usb_power_off.txt` 中 0403:6014 和 0403:6010 均不再出现在 Connected 列表。
3. 在上述 USB 消失检查之后又等待至少 10 秒；用户确认“已重新接电并连接 USB”。`usb_power_on.txt` 显示两组设备重新枚举，配置适配器 BUSID 从 3-4 变为 3-3。
4. 通过 Xilinx/1234-tulA 向 xc7a35t_0 重新下载先前验收通过的 50 MHz 镜像，SHA-256 为 `BCEC1C7476F1D2423144D9E25C7D188B484C4CA1DCEDDB71B18305B00F3DCDCA`。Hardware Manager 返回 PROGRAM_COMMAND=PASS、DONE=1、CRC_ERROR=0；完整控制台和器件属性见 `power_program/`。
5. 重新连接 FT232H fabric TAP（IDCODE=0x00000001，TCK=1MHz），初始状态及 job 全零；运行新的 FC 推理，输出再次为 `fbffffff0c000000dcffffff`，与参考一致。详见 `after_power_cycle/`。

断电证据来自用户观察和 USB 枚举，未用仪器测量电源轨降至零或放电曲线。此项证明断电后可通过重新下载恢复，不证明 Flash 自动启动。结束后 FPGA 保持已验证的 50 MHz 镜像，NPU 空闲，OpenOCD 已关闭、临时 USB 转发已释放。

## 证据与复现

`commands.jsonl.gz` 是无损 gzip 压缩的原始 RPC 日志；解压后每行包含时间、实际 Tcl 命令和返回值。`evidence_sha256.json` 记录归档文件和测试脚本哈希。前期尝试保留为诊断证据，结论只引用最终通过的 `busy_button_r5` 和两份 recovery smoke 报告。

```powershell
# 先下载验收通过镜像并启动 OpenOCD；运行后需人工按 RST/S6。
python verification/fpga/nocpu_busy_button.py --output build/busy_button_fresh
# 物理复位或重上电并下载后，检查全局初始状态和新推理。
python verification/fpga/nocpu_recovery_smoke.py --output build/recovery_fresh
```

## 电气波形待办

取得示波器/逻辑分析仪后，至少采集 TCK（H16）、TMS（C15）、TDI（E16）、TDO（D15）、系统复位（P15）及板上电源轨；保存原生采集文件、CSV、探头接法、时基与阈值。验证停钟区间、RST 断言/释放、TDO 对采样沿的建立保持，以及断电/上电轨迹。内部 50 MHz 和 busy 当前没有独立测试引脚，不能把 heartbeat LED 当作 busy；精确 busy-复位对齐需外部可观测信号或单独构建并重新验收的 ILA。此表为后续采集要求，没有生成或替代任何实测波形。
