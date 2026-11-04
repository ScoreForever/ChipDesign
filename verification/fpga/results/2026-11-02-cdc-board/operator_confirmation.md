# 现场操作确认

用户在当前对话中确认：“已按 RST/S6 并松开”。操作指令为按住约 1 秒后松开，保持电源与配置；未要求按 PROG/S5 或断电。按键定位依据厂商 EGo1 2.2 手册 §5.1：RST/S6 → FPGA_RESET/P15。

脚本观测时戳见 physical/arm.json 和 physical/check.json；观测结果 PASS，非默认哨兵和 job 计数被清除。按住时长依赖现场执行，未用示波器或 GPIO 采集器测量。
