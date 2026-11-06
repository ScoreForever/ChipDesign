# FPGA 历史复现工具

- `int4_v2/` 对应 [ABI 4.0 报告](../results/2026-11-04-int4-v2/report.md)，包括板级补测、模型回放、生产 RTL 回放、数据集重评和复核工具。复现请按报告的源码基线、工具版本、模型与镜像创建独立 checkout。部分脚本会读取旧分支的模型包或校验旧源码哈希。
- `nocpu_busy_button.py` 对应 [11 月 2 日的忙时按键实验](../results/2026-11-02-physical-followup/report.md)。其中构造长任务的方法依赖旧编译器允许扩大模型区，ABI 5.0 对容量的检查会拒绝该用法。现行忙时按键项目按 [签核表](../SIGNOFF_CHECKLIST.md) 记录。
- [CPU/GDB 历史流程](../CPU_GDB_HISTORY.md) 包含对应的仓库快照和证据入口。
- [完整测试集离线审计](../evidence/check_int4_v2_full_accuracy.py) 读取已归档的 ABI 4.0 预测与汇总文件。它也是 CI 的归档一致性检查。
