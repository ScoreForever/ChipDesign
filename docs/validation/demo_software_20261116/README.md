# 无板软件验收原始记录

总说明见 [软件交付报告](../../DEMO_SOFTWARE_20261116.md)。

- `summary.json`：执行环境、命令、检查结果和被验证的源码哈希。
- `all_golden.json`：六场景 80 个固定输入，逐项比较完整整数输出。
- `demo_tests.log`、`jtag_tests.log`：新增软件测试及既有驱动测试。
- `existing_golden.log`：原有七模型的 112 个整数参考黄金检查；不是分类标签全对。
- `checkpoint_export.log`：保存的两份 QAT 权重重新导出与 JSON/二进制一致。
- `har_raw.json`、`ecg_raw_initial.json`：原始测试窗口/记录的软件推理，`exact=null` 表示未提供逐位黄金输出。
- `phone_synthetic.json`、`kws_silence.json`、`mnist_image.json`：合成手机、静音 WAV 与合成数字文件路径测试。
- `face_host.json`、`blank_face.json`、`face_npu_image.json`、`emotion_image_v2.json`：图片链路与空帧测试；不据单图评估准确率。
- `public_data_sources.json`：实际下载的版本、大小、SHA-256；源数据和图片均不随提交发布。
- `SHA256SUMS.json`：本目录证据文件校验。文本固定 LF，避免 Windows/Git 换行转换改变校验。

`fpga_available=false` / `fpga_validated=false`。没有把模拟 NPU、软件计时或历史板测算作本轮新板测。
