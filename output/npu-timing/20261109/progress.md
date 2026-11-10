基线：上一轮已上板forward版，WNS+.031/WHS+.034，七模型15854/47176/95147/18836/86536/88754/37059，ABI5/8×8。保存baseline_core.sv和baseline_activation_store.sv。
候选spec：current/nowrap/wrap元数据并行，first_addr keep后晚选择，a_valid也选预归约any。15专项通过；81程序回归进行中，保持性能要求为七模型逐次周期及24计数不回退。
板卡协调：侧聊01a1bba4-ae9f-73a3-93b3-423fcc3dff61新获用户授权做1MHz RPC/USB/模型常驻对照，独占FT232H与OpenOCD6676约3–5分钟；收到释放通知前不操作FT232H、不启动OpenOCD、不下载镜像。当前只做RTL仿真/远端构建。

spec完整81程序+19负向/生命周期通过，所有cycles/24计数逐例与forward基线一致。15边界、8传输、18native及7模型trace-disabled生产重放通过。remote checkout_spec/build/timing_spec_50mhz，stage chipdesign_nocpu_zasr9zas，SSH61708。侧聊RPC/USB短测已结束并明确释放FT232H，baseline实测ECG15854/GTSRB95147符合当前基线；新镜像仍需时序≥目标、CDC后上板。

最新板卡协调：侧聊用户又授权host RPC批处理/模型常驻CLI优化A/B，暂占FT232H/OpenOCD6676；当前仍需等待这次明确释放（不能沿用先前释放）。不修改software/host文件，与侧聊源码分工隔离。board_lease.json当前released=false，program gate加入该检查。当前仅Win端Vivado综合/布局。

侧聊host批处理已完成并释放，84+3次旧镜像验证通过；生产软件仅改software/host/nocpu_jtag.py、新README及test_nocpu_rpc_batch.py，由侧聊维护，主线程不覆盖。板卡lease现released=true。当前进阶候选peek：一位置超前游标+已寄存下一请求完整metadata，初始化请求在现有K组准备结束时预装；当前issue时准备后续请求，保持II/周期。新增TB初始p0及peek_p==issue_p+1不变量检查。spec第一轮placement WNS-.273、issue_p0→controller rspCE24级；后端仍负，不再选作当前方向。

registered peek完整81+19负向通过，cycles/24计数逐例等于当前上板forward；15边界通过（含初始p0/peek对齐TB断言）。冻结20输入，checkout_peek/build/timing_peek_50mhz、stage chipdesign_nocpu_icjp4vk_、SSH99575。spec失败试验PID3884已按路径核验停止，旧GUI保留。当前production7、JTAG8、native18重放进行。软件由侧聊独立负责，已应用host RPC优化三文件，禁止回退/覆盖；主线程仅改core与tb_npu_program。最新板卡已释放，local board_lease=true（remote需同步），门禁仍WNS≥.344后才CDC/上板。

peek完整检查结束：81程序与全部24计数/周期等于当前硬件基线；19负向/生命周期、15专项、8端到端传输、18native、生产7模型关闭trace重放通过。ABI5、8x8固定；当前peek metadata在已有prepare结束和issue事件提前注册，不额外增周期。侧聊已应用host优化且明确释放板卡；不覆盖这三份软件改动。program gate现在指checkout_peek并要求≥.344，remote待同步最新版。

peek首轮route日志WNS+.527/WHS+.027/TNS0/THS0，超过.344目标。参数末索引缓存bound仅功能试验（81通过）未启动实现，保留bound_core.sv/summary后撤出工作区；当前恢复已经实现的peek_before_cached_bound.sv，20冻结输入逐项核对完全一致。等最终signoff/CDC再上板。

最终验收完成：WNS+.533、WHS+.028、TNS0；同构建CDC 100+32动态/109静态端点通过；门禁哈希验证后烧录，DONE1 CRC0。板上14模型运行和5恢复检查通过，336计数与RTL一致，七模型周期与forward不变。已归档README/bit/报告。
