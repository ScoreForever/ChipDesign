# ABI 5.0 JTAG 主机工具

当前主机使用 INT4 / 8×8 描述符格式。先按板卡连接配置启动 OpenOCD，加载
`software/host/nocpu_openocd.cfg`；默认 Tcl RPC 为本机 6666 端口，TCK 配置为 1 MHz。

每条逻辑命令默认用一个 RPC，在 OpenOCD 内执行完整的 pending 预检查、提交和应答轮询。
返回后仍检查字段宽度、完成状态、地址回显和 NPU 错误码。burst 数据通路保留，按每批最多
64 个字传输。ABI、描述符、寄存器地址与 RTL 均未改变。

## 单次推理

```sh
python3 software/host/nocpu_jtag.py infer --package build/program \
  --input build/input.bin --output build/result.bin
```

## 同一连接加载一次、连续推理

```sh
python3 software/host/nocpu_jtag.py infer-many --package build/program \
  --inputs build/input0.bin build/input1.bin build/input2.bin \
  --output build/infer_many_results
```

输出目录必须是新目录。输入按参数顺序串行处理，输出为 `0000.bin`、`0001.bin` 等；
`report.json` 记录成功加载次数、完成的输入/输出和主机耗时。出错后停止，已完成结果保留，
报告的 `passed` 为 false。此报告表示协议操作完成，不代替应用的 golden/精度验证。

SDK 已支持同一模型连续推理，`infer()` 本身不重新加载：

```python
npu.load_model(package)
for frame in frames:
    result = npu.infer(frame)
```

独立启动多次单次 CLI 会分别加载模型；连续应用可使用 `infer-many` 或上述常驻会话。

## 对照与错误处理

`--legacy-rpc` 显式选择原来分开的预检查/提交/轮询 RPC，burst 仍然启用。
SDK 对应 `JtagTransport(rpc, batch_rpc=False)`。默认不会在错误后自动切换或重放操作。

RPC 断连或超时意味着操作可能已经执行。先检查/恢复当前状态，再决定下一步；工具不会
自动重发 START、COMMIT 或其他命令。连接的 socket 超时应为服务端事务留出足够响应时间。

```sh
python3 -m unittest discover -s software/tests -p 'test_nocpu*.py'
```

批处理测试通过真实 `tclsh` 执行协议脚本，覆盖占用、地址错配、设备错误、overrun、
超时/断连和不重放语义；缺少 `tclsh` 时会明确跳过这些 Tcl 执行测试。
