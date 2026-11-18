# Demo 视觉模型

本目录包含两个使用当前 NPU QAT/编译器导出的 ABI 5、8×8 INT4 实验包。
`model.json`、`c8/model.bin`/`manifest.json`、`qat_state.pt`、`golden.json`、`train.log`、
`training_result.json` 和 `SHA256SUMS.json` 分别保存模型、可部署包、训练状态、固定输入、日志、指标与校验。

| 模型 | 输入 | 输出 | model bytes | 激活峰值 | 实测软件指标 |
|---|---|---|---:|---:|---|
| emotion | 48×48 灰度 i4 | 7 个 INT32 分数 | 6460 | 7488 B | FER2013 PrivateTest 54.5277%，3589 张 |
| face | 32×32 灰度 i4 | 4×4×5 个 INT32 网格值 | 6068 | 3328 B | IoU≥0.5：P=49.5146%、R=52.2184%、F1=50.8306% |

两者都只用四条硬件描述符。每个模型 8 个固定输入已核对纯 Python 与 PyTorch 整数参考的完整输出；
运行 `python software/zoo/check_demo_models.py` 可检查已保存的 QAT 状态重新导出后与发布 JSON/二进制完全一致。
未做本轮 FPGA/RTL 新模型执行或参与者验收，模型质量仍有限。

## 训练与评估范围

表情：FER2013 标准七类编号 angry/disgust/fear/happy/sad/surprise/neutral；
train=28709，PublicTest=3589 用于选择 checkpoint，PrivateTest=3589 单列结果。
三层 16/24/16 通道卷积加空间展开 FC；80 个 float epoch、35 个 QAT epoch，seed=17。
统一灰度、48×48 双线性缩放和 `pixel/127.5-1`；训练图像有水平翻转。
分数描述图像表情类别，不用于判断人的心理状态。

检测：WIDER 训练划分抽取 8000 张有有效标注的图片，产生 13563 个正/空背景近景裁剪；
官方 validation 中选取互不相交的 500/500 张图片，分别产生 837/839 个验证/评估裁剪。
最终评估有 586 个目标，TP=306、FP=312。候选阈值 0.6、NMS IoU 0.3、匹配 IoU 0.5，按分数做一对一匹配。
这是标注引导的近景裁剪任务，**不是 WIDER 官方全图 AP，也不是现场摄像头准确率**。
三层 16/24/32 通道卷积，末层共享缩放的原始 INT32 Conv 头；55 个 float epoch、30 个 QAT epoch，seed=17。
头部每格五项为 objectness logit、格内中心 x/y、归一化宽/高；坐标解码和 NMS 在电脑执行。

训练环境为 Python 3.12、PyTorch 2.6.0+cu124、RTX 4060 Laptop。数据和命令见各 `training_result.json`。
日志中的验证分数是 checkpoint 选择指标；模型训练可能因硬件/数值算法有差异，固定发布包则按哈希和完整整数输出复核。
新增 QAT 参数 `fc.spatial=H*W` 支持展平的空间头；`conv.last=True` 关闭最后 Conv 的 BN/Requant，
并让所有输出共享一个权重 scale，便于恢复候选框数值。现有网络配置保持原行为。

## 来源与引用

- [FER2013 原始竞赛](https://www.kaggle.com/c/challenges-in-representation-learning-facial-expression-recognition-challenge/data)，本次从 [Parquet 镜像](https://huggingface.co/datasets/Aaryan333/fer2013_train_publicTest_privateTest/tree/da8d1643649c86bfc2cffba5b744b51fdf329212) 获取，固定 train/PublicTest/PrivateTest 文件。参见 Goodfellow et al., *Challenges in Representation Learning*, 2013。
- [WIDER FACE](http://shuoyang1213.me/WIDERFACE/)；Yang et al., *WIDER FACE: A Face Detection Benchmark*, CVPR 2016；[课程使用的公开镜像](https://huggingface.co/datasets/CUHK-CSE/wider_face/tree/db171f1b7fedf4d3453e81297ff02f9915356d19) 标记 CC BY-NC-ND 4.0。未提交源图片或裁剪图片。
- 电脑框选使用 [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)，其模型目录为 MIT 许可。ONNX 显式下载，SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`；未将它称为本次 NPU 检测模型。
- [UCI HAR](https://doi.org/10.24432/C54S4K)，Reyes-Ortiz et al., 2013，CC BY 4.0；仅随 demo 提供训练均值/标准差，不包含原始惯性记录。
- [MIT-BIH Arrhythmia Database 1.0.0](https://physionet.org/content/mitdb/1.0.0/)，Moody and Mark；记录只存于下载缓存，未随本次提交发布。

公开数据的固定版本、文件大小和 SHA-256 在 [数据来源清单](../../../../docs/validation/demo_software_20261116/public_data_sources.json)。
本目录模型用于课程实验；上游数据和预训练资产保留各自来源说明。
