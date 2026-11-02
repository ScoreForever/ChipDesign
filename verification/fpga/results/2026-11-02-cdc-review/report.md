# INT4 TAP CDC 专项复核（2026-11-02）

对前次 50 MHz 已布线实现逐条复核：109 条 CDC-15 为 72 位请求邮箱和 37 位响应邮箱的 CE 控制采样路径。未发现功能错误。本次补足实际 TAP 的动态验证并增加严格端点审查；没有修改 RTL、没有生成 waiver，Vivado 原始警告仍为 109 条。

## 结构依据

请求端在 Update-DR 写入 payload、翻转 toggle 并设置 pending；pending 期间拒绝覆盖。目的端经过两级 req_sync 后检测变化，再采样完整总线。响应端写入响应邮箱并更新 ack_toggle；TCK 域经过两级 ack_sync 后采样响应。下一次请求只能在当前响应已被接收后提交，因此两个邮箱在接收采样前均保持稳定。TMS reset 不取消在途握手；全局复位异步置位、独立同步释放。

前次布线证据中，请求 72/72 位和响应 37/37 位均有 20 ns datapath max-delay，最差余量分别为 18.310 ns、18.535 ns；toggle 同步器第二级 setup/hold 余量为正。本次没有重新实现，物理结论只适用于该已布线版本。

## 新增验证与结果

之前的 tb_cdc_reset 测试通用 cdc_2phase，不能直接证明专用 TAP。新增 tb_npu_jtag_cdc 直接实例化 npu_jtag_transport，通过真实串行 IR/DR 扫描驱动。Icarus 仿真 PASS：

- 100 组确定性伪随机 TCK 半周期（3–130 ns 取值范围），系统时钟 20 ns；请求和响应逐位比较，检查恰好接受一次。
- 命令背压、pending 时重复请求与 overrun、源端 payload 保持。
- 停 TCK 后响应保持、pending 时 TMS reset。
- 命令未接受、已接受未应答、响应已发布未采样三个阶段的停钟异步复位，以及新事务恢复。

新增 run_npu_jtag_cdc.py 运行仿真，严格匹配原始报告的 109 对端点，核验物理路径数、20 ns payload 约束及同步器第二级非负余量。未知 Warning/Critical/Error 明细、丢失/重复端点或约束失效均导致失败。它保留原始警告，不宣称结构警告为零。review.json 保存输入 SHA-256，compile.log 和 sim.log 保存结果。

复现（仓库根目录，PATH 需包含 Python、iverilog、vvp；输出目录须尚不存在）：

```powershell
python verification/fpga/run_npu_jtag_cdc.py --output build/cdc_review_fresh --static-evidence verification/fpga/results/2026-11-01-no-cpu-int4/evidence/validated_50mhz
```

## 适用边界

数字仿真无法模拟模拟亚稳态，也不能替代 MTBF 分析。修改 RTL、约束或器件后必须重新生成物理证据。实际硬件停 TCK、忙时复位、物理复位按钮和断电恢复尚未新增实测。原通用 bench 的工具失败记录保留；本次解决的是实际 TAP 的动态验证缺口。
