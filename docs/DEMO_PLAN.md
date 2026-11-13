# ChipDesign Demo 计划

课程讲义《先进芯片-课程1(1).pdf》第 28 页的四个候选题为心电信号分类、手写数字识别、语音关键词识别、人体活动识别。手写数字与原计划重合，合计六项。下表记录计划中的交付内容。

| 场景 | 首版演示 | 模型和数据 |
| --- | --- | --- |
| 手写数字 | 画板实时显示笔迹，写完提交或停笔后触发。电脑裁剪、等比缩放、居中到 28×28，FPGA 返回 0–9 的分数。 | 复用 `mnist_l`。用鼠标和触控板实写样本验收笔宽、居中和多笔输入。 |
| 人脸框选 | 先用电脑实时检测并提供裁脸。另做 FPGA 框选模式，首版限定单人近景，片上输出候选框，电脑映射回原图。 | 电脑端用 [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)。FPGA 版用 [WIDER FACE](https://huggingface.co/datasets/CUHK-CSE/wider_face) 和笔记本实拍样本训练、验证小检测器。 |
| 表情分类 | 电脑检测并裁出单人脸，FPGA 分类。电脑用模型输出绘制类别分数，随画面更新。 | 用 [FER2013](https://www.kaggle.com/c/challenges-in-representation-learning-facial-expression-recognition-challenge/data) 训练 INT4 小 CNN，可用 [FER+](https://github.com/microsoft/FERPlus) 的多标注者分布。留出未参与训练的现场参与者测试。 |
| ECG 心拍分类 | 按时间播放带标注的真实心电记录，电脑在心拍位置截窗、降采样到 128 点并量化，FPGA 逐拍分类。界面标出当前窗口及板端类别。 | 复用 `ecg` 和 [MIT-BIH](https://physionet.org/content/mitdb/1.0.0/)。首版使用记录标注定位，后续可接带数字输出的单导联 ECG 设备，并重新验证信号、采样率和 R 峰定位。 |
| 语音关键词 | 按键后说 yes、no、up 或 down，电脑录音并提取 MFCC，FPGA 输出关键词。电脑按完整类别结果决定是否触发界面动作。 | 复用 `kws_l`，数据为 [Speech Commands v2](https://arxiv.org/abs/1804.03209)。录音前处理复用训练时的 16 kHz、1 秒、49×10 MFCC 和训练集归一化参数。 |
| 人体活动识别 | 先回放六类惯性信号，再让参与者在腰部固定手机现场采集。电脑对齐手机时间戳、单位和轴方向，以 50 Hz 形成 128 点窗口，FPGA 判定活动。 | 复用 `har` 和 [UCI HAR](https://archive.ics.uci.edu/dataset/240/human+activity+recognition+using+smartphones)。按训练前端构造身体加速度、总加速度和角速度共 9 路，并复用训练集逐轴归一化。首窗约 2.56 秒。 |

## 与课程示例的对应

- ECG 现有模型输出 AAMI 五类心拍，讲义示例输出二类。展示五类可以复用模型。若交付要求片上两个输出，先明确二类定义，再训练并验证二分类包。回放结果作为工程演示。
- KWS 现有模型输出 10 个关键词及 unknown、silence，讲义示例为 20×16×1 INT8 声学特征和四类输出。界面可以只为四个目标词触发动作。若要求原输入尺寸和四个片上输出，可从历史 [四分类 log-mel 训练记录](MODEL_TRAINING_RESULTS.md) 出发，重新导出 ABI 5 INT4 包并测精度。
- HAR 现有 1D CNN 消费 128×9 时序窗口，讲义示例为 64 维特征和 64→32→6 MLP。若需要复现讲义结构，可从 UCI HAR 特征中仅用训练集选 64 维，训练量化 MLP，同时实现和验证同一套实时特征提取。
- MNIST 的 28×28 输入和 10 类输出与讲义对应，当前模型采用项目现有 INT4 CNN。

## 实现顺序与验收

手写数字 Demo 的启动方式见 [桌面 Demo 使用说明](../software/host/demos/README.md)。

1. 用 [主机驱动](../software/host/nocpu_jtag.py) 打通画板和麦克风，切换场景时装模型，同一场景内复用驻留模型。展示板端预测、片上周期与主机往返耗时。
2. 用带标注的原始 ECG 与 HAR 记录打通预处理到 FPGA，并保留样本回放以便复现。HAR 随后接手机 IMU，先验收固定佩戴下的站立、坐、步行，再验收楼梯与躺卧。
3. 接入电脑实时框脸并训练 FPGA 表情模型。另做 FPGA 检测模式，验证无人、单人进出画面、近景移动和光照变化。首版分模式运行，因为 [ABI 5 装载新模型会替换旧模型](CPU_LESS_CONTROL.md)。
4. 对固定输入核对软件整数参考和板端输出，对现场输入记录采集、预处理、传输与推理耗时，分别评估数据集指标和现场表现。现有 [板测数据](../verification/fpga/results/2026-11-11-jtag-frequency/fine/timing.csv) 可作为驱动基线。
